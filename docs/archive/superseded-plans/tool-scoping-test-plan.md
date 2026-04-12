# Phase 0: Tool Scoping - Comprehensive Test Plan

**Document Type:** Test Plan
**Phase:** 0 - Tool Registry Refactoring
**Author:** Morgan Rodriguez, Senior QA Engineer & Test Automation Architect
**Date:** 2026-04-05
**Status:** Draft

---

## Executive Summary

This test plan provides comprehensive coverage for the Phase 0 Tool Scoping implementation, which refactors the global mutable `_TOOL_REGISTRY` dict to a class-based `ToolRegistry` with per-agent tool scoping.

### Quality Reviewer Concerns Addressed

| ID | Concern | Severity | Test Coverage |
|----|---------|----------|---------------|
| BC-001 | Backward compatibility with 33+ files using `_TOOL_REGISTRY` | CRITICAL | Regression Tests |
| TS-001 | Thread-safety implementation details | CRITICAL | Unit Tests (Stress) |
| MCP-001 | MCP tool registration integration | CRITICAL | Integration Tests |
| COV-001 | Incomplete test coverage (edge cases, concurrency, memory) | MODERATE | All Test Suites |
| PERF-001 | Performance impact analysis | MODERATE | Performance Tests |
| SEC-001 | Security testing (tool isolation, allowlist bypass prevention) | CRITICAL | Security Tests |

---

## 1. Test File Structure

```
tests/
├── unit/
│   └── agents/
│       ├── test_tool_registry.py          # Core ToolRegistry class tests
│       ├── test_tool_scoping.py           # Agent tool scoping logic
│       └── test_backward_compat_shim.py   # _TOOL_REGISTRY alias tests
├── integration/
│   ├── test_tool_scoping_integration.py   # Agent + ToolRegistry integration
│   ├── test_configurable_agent_tools.py   # YAML tool loading
│   └── test_mcp_tool_registration.py      # MCP mixin integration
├── regression/
│   ├── test_backward_compat.py            # Existing _TOOL_REGISTRY patterns
│   └── test_public_api.py                 # No breaking changes validation
├── performance/
│   ├── test_tool_registry_perf.py         # Lookup overhead benchmarks
│   ├── test_memory_footprint.py           # Per-agent registry memory
│   └── test_concurrent_access.py          # Concurrent access latency
└── security/
    ├── test_tool_isolation.py             # Agent tool access boundaries
    ├── test_allowlist_bypass.py           # Allowlist bypass prevention
    └── test_mcp_injection.py              # MCP tool injection attacks
```

---

## 2. Unit Tests (`tests/unit/agents/test_tool_registry.py`)

### 2.1 ToolRegistry Singleton Behavior

| Test Function | Purpose | Key Assertions |
|---------------|---------|----------------|
| `test_singleton_instance` | Verify singleton pattern | Same instance returned on multiple calls |
| `test_singleton_thread_safety` | Thread-safe singleton creation | Single instance under concurrent access |
| `test_reset_singleton` | Reset for test isolation | New instance after reset |

**Fixtures Required:**
```python
@pytest.fixture
def tool_registry():
    """Provide fresh ToolRegistry instance for each test."""
    registry = ToolRegistry.get_instance()
    yield registry
    registry.reset()  # Clean up for next test
```

### 2.2 Tool Registration/Unregistration

| Test Function | Purpose | Key Assertions |
|---------------|---------|----------------|
| `test_register_tool` | Basic tool registration | Tool in registry, metadata preserved |
| `test_unregister_tool` | Tool removal | Tool not in registry after removal |
| `test_register_duplicate` | Duplicate handling | No duplicate entries, warning logged |
| `test_unregister_nonexistent` | Missing tool handling | No error, graceful handling |
| `test_register_with_metadata` | Full metadata registration | All fields (name, description, params, atomic) preserved |
| `test_callable_function` | Registered function execution | Function callable, returns expected result |

**Key Assertions:**
```python
def test_register_tool(tool_registry):
    """Test basic tool registration."""
    @tool
    def sample_tool(x: int) -> int:
        """A sample tool."""
        return x * 2

    assert "sample_tool" in tool_registry
    assert tool_registry.get_tool("sample_tool")["name"] == "sample_tool"
    assert tool_registry.get_tool("sample_tool")["description"] == "A sample tool."
    assert callable(tool_registry.get_tool("sample_tool")["function"])
    result = tool_registry.execute_tool("sample_tool", x=5)
    assert result == 10
```

### 2.3 Agent Tool Scoping (allowed_tools filtering)

| Test Function | Purpose | Key Assertions |
|---------------|---------|----------------|
| `test_create_agent_scope` | Create scoped view for agent | Only allowed tools visible |
| `test_filter_tools_by_allowlist` | Allowlist filtering | Tools outside allowlist excluded |
| `test_scope_isolation` | Multiple agent scopes isolated | Changes in one scope don't affect others |
| `test_dynamic_scope_update` | Update allowed tools at runtime | Scope reflects changes immediately |
| `test_empty_allowlist` | Empty allowlist handling | No tools accessible |
| `test_wildcard_allowlist` | Wildcard patterns in allowlist | Pattern matching works correctly |

**Fixtures Required:**
```python
@pytest.fixture
def agent_scopes(tool_registry):
    """Create multiple agent scopes for isolation testing."""
    scope_a = tool_registry.create_scope("agent-a", allowed_tools=["tool1", "tool2"])
    scope_b = tool_registry.create_scope("agent-b", allowed_tools=["tool2", "tool3"])
    return {"a": scope_a, "b": scope_b}
```

### 2.4 Thread-Safety Stress Tests

| Test Function | Purpose | Key Assertions |
|---------------|---------|----------------|
| `test_concurrent_registration` | Simultaneous tool registration | No race conditions, all tools registered |
| `test_concurrent_execution` | Simultaneous tool execution | No deadlocks, all executions complete |
| `test_concurrent_scope_access` | Multiple scopes accessed concurrently | No cross-scope contamination |
| `test_stress_high_contention` | High contention scenario (100 threads) | System remains stable, no crashes |
| `test_lock_timeout` | Lock timeout behavior | No infinite waits, proper timeout handling |

**Performance Thresholds:**
```python
def test_stress_high_contention(tool_registry):
    """Stress test with 100 concurrent threads."""
    import threading
    import time

    errors = []
    results = []

    def worker(thread_id):
        try:
            tool_name = f"tool_{thread_id}"
            tool_registry.register_tool(tool_name, lambda: thread_id, f"Tool {thread_id}", {})
            result = tool_registry.execute_tool(tool_name)
            results.append(result)
        except Exception as e:
            errors.append((thread_id, e))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(100)]
    start = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    elapsed = time.time() - start

    assert len(errors) == 0, f"Errors occurred: {errors}"
    assert len(results) == 100, f"Missing results: {100 - len(results)}"
    assert elapsed < 5.0, f"Too slow: {elapsed}s"  # Threshold: 5 seconds
```

### 2.5 Backward Compatibility Shim (_TOOL_REGISTRY alias)

| Test Function | Purpose | Key Assertions |
|---------------|---------|----------------|
| `test_global_alias_exists` | _TOOL_REGISTRY still accessible | Alias points to registry |
| `test_dict_like_access` | Dict-style access works | `registry["tool"]` syntax works |
| `test_dict_like_setitem` | Dict-style assignment works | `registry["tool"] = info` works |
| `test_dict_like_del` | Dict-style deletion works | `del registry["tool"]` works |
| `test_dict_keys_values` | keys(), values(), items() work | Returns correct views |
| `test_deprecation_warning` | Deprecation warning on access | Warning logged on each access |

**Implementation Note:**
```python
class _ToolRegistryAlias(dict):
    """Backward-compatible dict-like wrapper around ToolRegistry."""

    def __getitem__(self, key):
        warnings.warn(
            "_TOOL_REGISTRY dict access is deprecated. Use ToolRegistry.get_instance() instead.",
            DeprecationWarning,
            stacklevel=2
        )
        return ToolRegistry.get_instance().get_tool(key)

    # Implement other dict methods similarly...
```

---

## 3. Integration Tests (`tests/integration/test_tool_scoping.py`)

### 3.1 Agent + ToolRegistry Integration

| Test Function | Purpose | Key Assertions |
|---------------|---------|----------------|
| `test_agent_initialization_with_registry` | Agent initializes with ToolRegistry | Agent has access to scoped tools |
| `test_agent_tool_execution` | Agent executes scoped tool | Tool executes, result returned |
| `test_agent_blocked_tool_access` | Agent blocked from unauthorized tool | Security violation raised |
| `test_multiple_agents_isolation` | Multiple agents with different scopes | No tool leakage between agents |

### 3.2 ConfigurableAgent YAML Tool Loading

| Test Function | Purpose | Key Assertions |
|---------------|---------|----------------|
| `test_load_tools_from_yaml` | Load tools specified in YAML | All declared tools loaded |
| `test_missing_tool_warning` | Tool not found in registry | Warning logged, execution continues |
| `test_tool_module_loading` | Load tool from module path | Module imported, tool registered |
| `test_mcp_tool_in_yaml` | MCP tools in YAML allowlist | MCP tools properly resolved |
| `test_yaml_allowlist_enforcement` | YAML allowlist enforced at runtime | Only allowlisted tools accessible |

**Test Data (YAML fixture):**
```yaml
# tests/fixtures/test-agent.yaml
agent:
  id: test-agent
  name: Test Agent
  tools:
    - file_read
    - file_write
    - bash_execute
```

### 3.3 MCP Mixture Tool Registration

| Test Function | Purpose | Key Assertions |
|---------------|---------|----------------|
| `test_mcp_server_connection` | Connect MCP server | Server connected, tools listed |
| `test_mcp_tool_registration` | MCP tools auto-registered | Tools in global registry |
| `test_mcp_tool_scoping` | MCP tools scoped to agent | Only allowlisted MCP tools accessible |
| `test_mcp_server_disconnect` | Disconnect MCP server | Tools unregistered, scope updated |
| `test_mcp_tool_name_resolution` | Unprefixed tool names resolved | `get_time` -> `mcp_time_get_current_time` |

### 3.4 Multi-Agent Tool Isolation

| Test Function | Purpose | Key Assertions |
|---------------|---------|----------------|
| `test_concurrent_agent_execution` | Multiple agents run simultaneously | No tool interference |
| `test_agent_tool_cross_access_attempt` | Agent A tries to call Agent B's tool | Access denied, security violation logged |
| `test_dynamic_tool_addition_isolation` | Add tool to Agent A at runtime | Agent B doesn't see new tool |
| `test_agent_scope_cleanup` | Agent terminates, scope cleaned up | No leftover state in registry |

---

## 4. Regression Tests (`tests/regression/test_backward_compat.py`)

### 4.1 Existing `_TOOL_REGISTRY` Usage Patterns

| Test Function | Purpose | Key Assertions |
|---------------|---------|----------------|
| `test_existing_import_pattern` | `from gaia.agents.base.tools import _TOOL_REGISTRY` | Import succeeds |
| `test_existing_check_pattern` | `if tool_name in _TOOL_REGISTRY` | Membership check works |
| `test_existing_iteration_pattern` | `for name, info in _TOOL_REGISTRY.items()` | Iteration works |
| `test_existing_get_pattern` | `_TOOL_REGISTRY.get(tool_name)` | `.get()` method works |
| `test_direct_registry_modification` | `_TOOL_REGISTRY[name] = info` | Assignment works with warning |

**Files Using These Patterns (from grep):**
- `src/gaia/ui/_chat_helpers.py` - Line 45: `if tool_name in _TOOL_REGISTRY`
- `src/gaia/agents/configurable.py` - Line 146: `if tool_name in _TOOL_REGISTRY`
- `src/gaia/mcp/mixin.py` - Line 329: `_TOOL_REGISTRY[gaia_name] = gaia_tool`
- `src/gaia/agents/code/agent.py` - Line 89: `for name, info in _TOOL_REGISTRY.items()`
- `tests/unit/test_tool_decorator.py` - Line 22: `_TOOL_REGISTRY.clear()`

### 4.2 Dict-like Access Compatibility

| Test Function | Purpose | Key Assertions |
|---------------|---------|----------------|
| `test_dict_subscript_get` | `_TOOL_REGISTRY[key]` | Returns tool info |
| `test_dict_subscript_set` | `_TOOL_REGISTRY[key] = value` | Adds/updates tool |
| `test_dict_contains` | `key in _TOOL_REGISTRY` | Returns True/False |
| `test_dict_len` | `len(_TOOL_REGISTRY)` | Returns count |
| `test_dict_iter` | `for key in _TOOL_REGISTRY` | Iterates keys |

### 4.3 Deprecation Warning Firing

| Test Function | Purpose | Key Assertions |
|---------------|---------|----------------|
| `test_warning_on_getitem` | Warning on `_TOOL_REGISTRY[key]` | DeprecationWarning raised |
| `test_warning_on_setitem` | Warning on assignment | DeprecationWarning raised |
| `test_warning_on_contains` | Warning on `in` check | DeprecationWarning raised |
| `test_warning_includes_message` | Warning message is descriptive | Message mentions ToolRegistry |
| `test_warning_stacklevel` | Warning points to user code | stacklevel=2 for correct frame |

### 4.4 No Breaking Changes to Public API

| Test Function | Purpose | Key Assertions |
|---------------|---------|----------------|
| `test_all_exports_available` | All public exports still available | `tool`, `_TOOL_REGISTRY`, `get_tool_display_name` |
| `test_decorator_syntax_unchanged` | `@tool` and `@tool()` work | Both syntaxes functional |
| `test_tool_metadata_captured` | Tool decorator captures metadata | name, description, params, atomic all present |
| `test_existing_tests_pass` | All existing unit tests pass | No test failures |

**Test Execution:**
```bash
# Run all existing tests that use _TOOL_REGISTRY
python -m pytest tests/unit/test_tool_decorator.py -v
python -m pytest tests/unit/test_file_tools.py -v
python -m pytest tests/test_external_tools.py -v
python -m pytest tests/test_typescript_tools.py -v
```

---

## 5. Performance Tests (`tests/performance/test_tool_registry_perf.py`)

### 5.1 Benchmark: Dict vs. Class Lookup Overhead

| Test Function | Purpose | Threshold |
|---------------|---------|-----------|
| `test_dict_lookup_baseline` | Baseline: raw dict lookup | < 0.1 μs |
| `test_registry_lookup_overhead` | ToolRegistry.get_tool() overhead | < 1.0 μs |
| `test_scoped_lookup_overhead` | Scoped lookup (with filtering) | < 5.0 μs |
| `test_lookup_under_load` | Lookup latency under concurrent load | < 10.0 μs (p99) |

**Benchmark Code:**
```python
import pytest
import time

def test_registry_lookup_overhead(tool_registry):
    """Benchmark ToolRegistry lookup vs. raw dict."""
    # Register 1000 tools
    for i in range(1000):
        tool_registry.register_tool(f"tool_{i}", lambda: i, f"Tool {i}", {})

    # Baseline: raw dict
    raw_dict = {f"tool_{i}": {"name": f"tool_{i}"} for i in range(1000)}
    start = time.perf_counter()
    for _ in range(10000):
        _ = raw_dict["tool_500"]
    dict_time = time.perf_counter() - start

    # ToolRegistry lookup
    start = time.perf_counter()
    for _ in range(10000):
        _ = tool_registry.get_tool("tool_500")
    registry_time = time.perf_counter() - start

    # Overhead should be < 10x (allowing for method call overhead)
    overhead_ratio = registry_time / dict_time
    assert overhead_ratio < 10, f"Lookup overhead too high: {overhead_ratio}x"
```

### 5.2 Memory Per-Agent Registry Footprint

| Test Function | Purpose | Threshold |
|---------------|---------|-----------|
| `test_memory_per_scope` | Memory cost per agent scope | < 1 KB per scope |
| `test_memory_100_agents` | Memory for 100 agent scopes | < 100 KB total |
| `test_memory_cleanup` | Memory freed after scope removal | < 10% leak |
| `test_memory_large_registry` | Memory with 10000 tools | < 50 MB total |

**Measurement Approach:**
```python
import tracemalloc

def test_memory_per_scope(tool_registry):
    """Measure memory footprint per agent scope."""
    tracemalloc.start()

    # Baseline memory
    baseline = tracemalloc.get_traced_memory()[0]

    # Create 100 scopes
    scopes = []
    for i in range(100):
        scope = tool_registry.create_scope(f"agent_{i}", allowed_tools=["tool1", "tool2"])
        scopes.append(scope)

    # Memory after scopes
    after_scopes = tracemalloc.get_traced_memory()[0]
    per_scope_kb = (after_scopes - baseline) / 100 / 1024

    assert per_scope_kb < 1.0, f"Memory per scope too high: {per_scope_kb} KB"

    tracemalloc.stop()
```

### 5.3 System Prompt Formatting Time

| Test Function | Purpose | Threshold |
|---------------|---------|-----------|
| `test_format_tools_for_prompt` | Format tools for system prompt | < 1 ms for 10 tools |
| `test_format_prompt_large_registry` | Format with 1000 tools | < 50 ms |
| `test_format_scoped_prompt` | Format scoped tools (5 allowed) | < 0.5 ms |

### 5.4 Concurrent Access Latency

| Test Function | Purpose | Threshold |
|---------------|---------|-----------|
| `test_concurrent_read_latency` | Concurrent tool lookups | p99 < 10 μs |
| `test_concurrent_write_latency` | Concurrent tool registrations | p99 < 100 μs |
| `test_read_write_contention` | Mixed read/write workload | p99 < 50 μs |
| `test_lock_contention_under_load` | Lock wait time under load | < 1 ms average |

---

## 6. Security Tests (`tests/security/test_tool_isolation.py`)

### 6.1 Agent Tool Access Boundary Enforcement

| Test Function | Purpose | Key Assertions |
|---------------|---------|----------------|
| `test_scope_blocks_unauthorized_tool` | Agent blocked from tool not in allowlist | Access denied exception |
| `test_scope_allows_authorized_tool` | Agent can access allowlisted tool | Tool executes successfully |
| `test_scope_boundary_cross_agent` | Agent A cannot access Agent B's tools | Cross-agent access denied |
| `test_scope_boundary_mcp_tools` | MCP tools properly scoped | MCP tool namespacing enforced |
| `test_empty_scope_blocks_all` | Empty allowlist blocks all tools | No tools accessible |

**Security Test Implementation:**
```python
def test_scope_blocks_unauthorized_tool(tool_registry):
    """Verify that agents cannot access tools outside their allowlist."""
    # Create scope with limited tools
    scope = tool_registry.create_scope("restricted-agent", allowed_tools=["file_read"])

    # Register additional tool
    tool_registry.register_tool("file_write", lambda: "written", "Write file", {})

    # Attempt to execute unauthorized tool
    with pytest.raises(ToolAccessDeniedError) as exc_info:
        scope.execute_tool("file_write", path="test.txt")

    assert "unauthorized" in str(exc_info.value).lower()
    assert "security_violation" in exc_info.value.metadata
```

### 6.2 Allowlist Bypass Attempt Detection

| Test Function | Purpose | Key Assertions |
|---------------|---------|----------------|
| `test_case_manipulation_bypass` | Try `FILE_READ` vs `file_read` | Case normalization doesn't bypass |
| `test_prefix_injection_bypass` | Try `mcp_fake_file_read` | Fake prefix rejected |
| `test_unicode_confusion_bypass` | Try unicode lookalikes | Unicode confusion rejected |
| `test_partial_name_match_bypass` | Try `file` to match `file_read` | Partial match rejected |
| `test_alias_bypass_attempt` | Try tool aliases not in allowlist | Alias rejected if not mapped |

### 6.3 MCP Tool Injection Attack Prevention

| Test Function | Purpose | Key Assertions |
|---------------|---------|----------------|
| `test_mcp_unregistered_server` | MCP server not in allowlist | Connection rejected |
| `test_mcp_dynamic_tool_addition` | MCP server adds tool at runtime | New tool not accessible until allowlist updated |
| `test_mcp_server_impersonation` | Fake server with same name | Impersonation detected, rejected |
| `test_mcp_tool_name_collision` | MCP tool name collides with native | Native tool takes precedence or error raised |

### 6.4 Cross-Agent Tool Leakage

| Test Function | Purpose | Key Assertions |
|---------------|---------|----------------|
| `test_no_leak_concurrent_execution` | Two agents execute simultaneously | No tool results leaked between agents |
| `test_no_leak_scope_switching` | Switch agent contexts rapidly | No state leakage between switches |
| `test_no_leak_after_scope_removal` | Agent scope removed | No access to previously allowed tools |
| `test_no_leak_exception_handling` | Exception during tool execution | No partial state leakage |

---

## 7. Fixture Requirements

### 7.1 Shared Fixtures (`tests/conftest.py`)

```python
import pytest
from gaia.agents.base.tools import ToolRegistry, _TOOL_REGISTRY

@pytest.fixture(scope="function")
def tool_registry():
    """Provide fresh ToolRegistry instance for each test."""
    registry = ToolRegistry.get_instance()
    registry.reset()  # Clear any existing state
    yield registry
    registry.reset()  # Clean up for next test

@pytest.fixture(scope="function")
def registered_tools(tool_registry):
    """Register common test tools."""
    @tool_registry.register_tool
    def file_read(path: str) -> str:
        """Read a file."""
        return f"Content of {path}"

    @tool_registry.register_tool
    def file_write(path: str, content: str) -> str:
        """Write a file."""
        return f"Wrote {len(content)} bytes to {path}"

    @tool_registry.register_tool(atomic=True)
    def bash_execute(command: str) -> str:
        """Execute bash command."""
        return f"Executed: {command}"

    yield ["file_read", "file_write", "bash_execute"]

    # Cleanup
    for tool in ["file_read", "file_write", "bash_execute"]:
        tool_registry.unregister_tool(tool)

@pytest.fixture
def agent_scopes(tool_registry, registered_tools):
    """Create multiple agent scopes for isolation testing."""
    scope_a = tool_registry.create_scope("agent-a", allowed_tools=["file_read", "file_write"])
    scope_b = tool_registry.create_scope("agent-b", allowed_tools=["file_read", "bash_execute"])
    return {"a": scope_a, "b": scope_b}
```

### 7.2 MCP Integration Fixtures

```python
@pytest.fixture
def mock_mcp_server():
    """Mock MCP server for integration testing."""
    # Start mock MCP server process
    server = MockMCPServer(port=9999)
    server.start()
    yield server
    server.stop()

@pytest.fixture
def mcp_connected_agent(tool_registry, mock_mcp_server):
    """Agent with MCP server connected."""
    scope = tool_registry.create_scope("mcp-agent", allowed_tools=[
        "file_read",
        "mcp_github_get_issue",  # MCP tool
    ])
    yield scope
    tool_registry.remove_scope("mcp-agent")
```

### 7.3 Performance Testing Fixtures

```python
@pytest.fixture
def large_registry(tool_registry):
    """ToolRegistry with 10000 tools for performance testing."""
    for i in range(10000):
        tool_registry.register_tool(
            f"tool_{i}",
            lambda i=i: i,
            f"Tool {i}",
            {"i": {"type": "integer", "required": True}}
        )
    yield tool_registry
    tool_registry.reset()
```

---

## 8. Performance Benchmarks Summary

| Metric | Baseline | Threshold | Measurement |
|--------|----------|-----------|-------------|
| Dict lookup | 0.1 μs | - | Raw dict |
| Registry lookup | - | < 1.0 μs | ToolRegistry.get_tool() |
| Scoped lookup | - | < 5.0 μs | Scope.get_tool() |
| Memory per scope | - | < 1 KB | tracemalloc |
| Prompt format (10 tools) | - | < 1 ms | time.perf_counter() |
| Prompt format (1000 tools) | - | < 50 ms | time.perf_counter() |
| Concurrent p99 latency | - | < 10 μs | 100 threads |
| Lock contention avg | - | < 1 ms | threading.Lock timing |

---

## 9. Security Test Scenarios Summary

| Scenario | Attack Vector | Expected Outcome |
|----------|---------------|------------------|
| Unauthorized tool access | Direct call to blocked tool | AccessDeniedError |
| Case manipulation | `FILE_READ` vs `file_read` | Rejected (case-insensitive match to allowlist only) |
| Prefix injection | `mcp_fake_tool` | Rejected (not in allowlist) |
| Unicode confusion | `file_read` with unicode chars | Rejected (exact match required) |
| Partial name match | `file` to match `file_read` | Rejected (exact match required) |
| MCP server impersonation | Fake server named "github" | Rejected (server not in config) |
| Dynamic tool injection | MCP server adds tool at runtime | Not accessible until allowlist updated |
| Cross-agent leakage | Agent A accesses Agent B's scope | Rejected (scope isolation) |

---

## 10. Test Execution Plan

### Phase 1: Unit Tests (Day 1-2)
```bash
# Run all unit tests for ToolRegistry
python -m pytest tests/unit/agents/test_tool_registry.py -v --tb=short
python -m pytest tests/unit/agents/test_tool_scoping.py -v --tb=short
python -m pytest tests/unit/agents/test_backward_compat_shim.py -v --tb=short
```

### Phase 2: Integration Tests (Day 2-3)
```bash
# Run integration tests
python -m pytest tests/integration/test_tool_scoping_integration.py -v --tb=short
python -m pytest tests/integration/test_configurable_agent_tools.py -v --tb=short
python -m pytest tests/integration/test_mcp_tool_registration.py -v --tb=short
```

### Phase 3: Regression Tests (Day 3)
```bash
# Run regression tests for backward compatibility
python -m pytest tests/regression/test_backward_compat.py -v --tb=short
python -m pytest tests/regression/test_public_api.py -v --tb=short

# Verify existing tests still pass
python -m pytest tests/unit/test_tool_decorator.py -v --tb=short
```

### Phase 4: Performance Tests (Day 4)
```bash
# Run performance benchmarks
python -m pytest tests/performance/test_tool_registry_perf.py -v --tb=short
python -m pytest tests/performance/test_memory_footprint.py -v --tb=short
python -m pytest tests/performance/test_concurrent_access.py -v --tb=short
```

### Phase 5: Security Tests (Day 4-5)
```bash
# Run security tests
python -m pytest tests/security/test_tool_isolation.py -v --tb=short
python -m pytest tests/security/test_allowlist_bypass.py -v --tb=short
python -m pytest tests/security/test_mcp_injection.py -v --tb=short
```

### Full Test Suite
```bash
# Run all Phase 0 tests
python -m pytest tests/unit/agents/ tests/integration/ tests/regression/ tests/performance/ tests/security/ -v --tb=short --cov=gaia.agents.base.tools
```

---

## 11. Coverage Requirements

| Component | Minimum Coverage | Critical Paths |
|-----------|------------------|----------------|
| ToolRegistry class | 95% | Singleton, register, unregister, get_tool |
| AgentScope class | 95% | Tool filtering, execute_tool, isolation |
| Backward compat shim | 90% | Dict-like methods, deprecation warnings |
| MCP integration | 85% | Tool registration, name resolution |
| Security enforcement | 100% | Allowlist validation, access control |

---

## 12. Exit Criteria

Phase 0 is complete when:

- [ ] All unit tests pass (100% pass rate)
- [ ] All integration tests pass (100% pass rate)
- [ ] All regression tests pass (no breaking changes)
- [ ] Performance thresholds met (all benchmarks under threshold)
- [ ] Security tests pass (all attack vectors blocked)
- [ ] Code coverage >= 90% overall
- [ ] No CRITICAL or HIGH severity issues open
- [ ] Existing tests using `_TOOL_REGISTRY` pass unmodified

---

## 13. Risk Assessment

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| Backward compatibility break | Medium | High | Extensive regression tests, deprecation warnings |
| Performance regression | Low | Medium | Performance benchmarks, thresholds enforced |
| Security bypass discovered | Low | Critical | Security review, penetration testing |
| MCP integration issues | Medium | Medium | Early MCP team collaboration, integration tests |
| Thread safety bugs | Medium | High | Stress tests, concurrent access patterns |

---

## 14. Handoff

**Handoff to:** planning-analysis-strategist

**Purpose:** Refine test approach based on test requirements and implementation plan alignment.

**Key Questions:**
1. Are there additional edge cases in the implementation plan not covered by these tests?
2. Should the test plan include end-to-end pipeline validation (Phase 0 tools working in full pipeline)?
3. Are there specific MCP servers that need priority testing?
4. What is the target timeline for Phase 0 completion?

---

## Appendix A: Files Using _TOOL_REGISTRY (from grep)

Total: 38 files using `_TOOL_REGISTRY`. Key files:

### Core Implementation (7 files)
1. `src/gaia/agents/base/tools.py` - Tool registry and decorator
2. `src/gaia/agents/base/agent.py` - Base agent tool execution
3. `src/gaia/agents/base/__init__.py` - Export _TOOL_REGISTRY
4. `src/gaia/agents/configurable.py` - ConfigurableAgent tool loading
5. `src/gaia/agents/code/agent.py` - CodeAgent implementation
6. `src/gaia/agents/chat/agent.py` - ChatAgent implementation
7. `src/gaia/mcp/mixin.py` - MCP tool registration

### UI/API (3 files)
8. `src/gaia/ui/server.py` - Agent UI backend
9. `src/gaia/ui/_chat_helpers.py` - Chat helper functions
10. `src/gaia/tools/__init__.py` - Tools package init

### Tests (8 files)
11. `tests/unit/test_tool_decorator.py` - Tool decorator tests
12. `tests/unit/test_file_tools.py` - File tool tests
13. `tests/unit/test_emr_agent.py` - EMR agent tests
14. `tests/test_external_tools.py` - External tool tests
15. `tests/test_typescript_tools.py` - TypeScript tool tests
16. `tests/test_code_agent.py` - Code agent tests
17. `tests/test_code_agent_mixins.py` - Code agent mixins
18. `tests/test_hardware_advisor_agent.py` - Hardware advisor tests

---

**Document Version:** 1.0
**Last Updated:** 2026-04-05
**Review Status:** Pending review by planning-analysis-strategist
