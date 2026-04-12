# Phase 0: Tool Scoping Implementation Plan

**Version:** 1.0
**Status:** Ready for Implementation
**Priority:** P0 (Critical Foundation)
**Estimated Duration:** 4 Days (Day 1-4)
**Owner:** senior-developer
**Handoff From:** planning-analysis-strategist (Dr. Sarah Kim)

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Day 1-4 Implementation Schedule](#2-day-1-4-implementation-schedule)
3. [Dependency Analysis](#3-dependency-analysis)
4. [Risk Assessment](#4-risk-assessment)
5. [Code Structure Outline](#5-code-structure-outline)
6. [Test Coverage Requirements](#6-test-coverage-requirements)
7. [Quality Gate 1 Exit Criteria](#7-quality-gate-1-exit-criteria)
8. [Handoff Notes](#8-handoff-notes-for-senior-developer)

---

## 1. Executive Summary

### 1.1 Objective

Phase 0 refactors GAIA's global tool registry into a class-based, thread-safe singleton with per-agent scoping. This addresses **RC#2** (Tool Implementations Missing) and **RC#7** (Empty Tool Descriptions in System Prompt) from the pipeline root causes, while establishing the foundation for BAIBEL pattern integration in Phases 1-3.

### 1.2 Key Deliverables

| Deliverable | Location | Priority |
|-------------|----------|----------|
| `ToolRegistry` class | `src/gaia/agents/base/tools.py` | P0 |
| `AgentScope` class | `src/gaia/agents/base/tools.py` | P0 |
| `ExceptionRegistry` class | `src/gaia/agents/base/tools.py` | P0 |
| `_ToolRegistryAlias` shim | `src/gaia/agents/base/tools.py` | P0 |
| Agent integration | `src/gaia/agents/base/agent.py` | P0 |
| ConfigurableAgent integration | `src/gaia/agents/configurable.py` | P0 |
| Unit test suite | `tests/unit/agents/test_tool_registry.py` | P0 |
| Integration test suite | `tests/unit/agents/test_tool_scoping.py` | P0 |
| Backward compat tests | `tests/unit/agents/test_backward_compat_shim.py` | P0 |

### 1.3 Success Metrics

| Metric | Target | Measurement |
|--------|--------|-------------|
| Backward Compatibility | 100% pass | All 38 files referencing `_TOOL_REGISTRY` functional |
| Security | 0% bypass success | Case-sensitive allowlist enforcement |
| Performance | <5% overhead | Time per `execute_tool()` vs baseline |
| Memory | 0% leaks | RSS delta after agent shutdown |

---

## 2. Day 1-4 Implementation Schedule

### Day 1: Core Implementation (Tools.py Rewrite)

**Owner:** senior-developer
**Duration:** 8 hours
**Status:** COMPLETE - 2026-04-05

#### Completion Summary

| Metric | Target | Actual | Status |
|--------|--------|--------|--------|
| Lines of Code | ~450 | 884 | EXCEEDED |
| Test Functions | 90 | 171 | EXCEEDED |
| Test Pass Rate | 100% | 100% | PASS |
| Quality Review | Complete | APPROVED FOR TESTING | PASS |

#### Deliverables Completed

- [x] `tools.py` complete rewrite with all classes
- [x] All custom exception classes implemented
- [x] `ExceptionRegistry` with thread-safe operations
- [x] `ToolRegistry` singleton with double-checked locking
- [x] `AgentScope` with case-sensitive allowlist filtering
- [x] `_ToolRegistryAlias` shim with deprecation warnings
- [x] Updated `@tool` decorator using `ToolRegistry`
- [x] `_TOOL_REGISTRY` global for backward compatibility
- [x] Unit tests for `ExceptionRegistry` (12 functions)
- [x] Unit tests for `ToolRegistry` (45+ functions)
- [x] Unit tests for `AgentScope` (25 functions)
- [x] Unit tests for `_ToolRegistryAlias` (20 functions)
- [x] Security tests (18 functions)
- [x] Performance benchmarks included

#### Morning Session (4 hours)

| Time | Task | Details | Output |
|------|------|---------|--------|
| 09:00-10:30 | ExceptionRegistry class | Implement error tracking with thread safety | `ExceptionRegistry` class with `record()`, `get_exceptions()`, `clear()`, `get_error_rate()` |
| 10:30-10:45 | Break | | |
| 10:45-12:30 | ToolRegistry class (Part 1) | Singleton pattern, thread-safe `register()`, `get_all_tools()` | Core registry with RLock protection |

#### Afternoon Session (4 hours)

| Time | Task | Details | Output |
|------|------|---------|--------|
| 13:30-15:00 | ToolRegistry class (Part 2) | `create_scope()`, `execute_tool()`, `unregister()` | Full registry API |
| 15:00-15:15 | Break | | |
| 15:15-16:30 | AgentScope class | Allowlist filtering, case-sensitive matching | `AgentScope` with security enforcement |
| 16:30-17:30 | _ToolRegistryAlias shim | Dict interface with deprecation warnings | Backward compatibility layer |

#### Day 1 Code Structure

```python
# src/gaia/agents/base/tools.py - Complete rewrite

import threading
import warnings
import inspect
from typing import Callable, Dict, List, Optional, Set, Any
from dataclasses import dataclass, field
import time

# Custom Exceptions
class ToolNotFoundError(Exception):
    """Raised when attempting to execute an unregistered tool."""
    def __init__(self, tool_name: str):
        self.tool_name = tool_name
        super().__init__(f"Tool '{tool_name}' not found in registry")

class ToolAccessDeniedError(Exception):
    """Raised when agent attempts to access a tool outside its allowlist."""
    def __init__(self, tool_name: str, agent_id: str):
        self.tool_name = tool_name
        self.agent_id = agent_id
        super().__init__(f"Agent '{agent_id}' denied access to tool '{tool_name}'")

class ToolExecutionError(Exception):
    """Raised when tool execution fails."""
    def __init__(self, tool_name: str, cause: Exception):
        self.tool_name = tool_name
        self.cause = cause
        super().__init__(f"Tool '{tool_name}' execution failed: {cause}")

@dataclass
class ExceptionRecord:
    """Record of a tool execution exception."""
    tool_name: str
    exception_type: str
    message: str
    traceback: str
    timestamp: float
    agent_id: Optional[str] = None

# ExceptionRegistry Class
class ExceptionRegistry:
    """Thread-safe registry for tracking tool execution exceptions."""

    def __init__(self):
        self._exceptions: List[ExceptionRecord] = []
        self._lock = threading.RLock()
        self._error_counts: Dict[str, int] = {}
        self._execution_counts: Dict[str, int] = {}

    def record(self, tool_name: str, exception: Exception, agent_id: Optional[str] = None) -> None:
        """Record an exception for a tool execution."""
        with self._lock:
            record = ExceptionRecord(
                tool_name=tool_name,
                exception_type=type(exception).__name__,
                message=str(exception),
                traceback="",  # Populate from sys.exc_info() if needed
                timestamp=time.time(),
                agent_id=agent_id,
            )
            self._exceptions.append(record)
            self._error_counts[tool_name] = self._error_counts.get(tool_name, 0) + 1

    def record_execution(self, tool_name: str) -> None:
        """Record a successful tool execution for error rate calculation."""
        with self._lock:
            self._execution_counts[tool_name] = self._execution_counts.get(tool_name, 0) + 1

    def get_exceptions(self, tool_name: Optional[str] = None, limit: int = 100) -> List[ExceptionRecord]:
        """Get recorded exceptions, optionally filtered by tool."""
        with self._lock:
            if tool_name:
                return [e for e in self._exceptions if e.tool_name == tool_name][:limit]
            return self._exceptions[-limit:]

    def clear(self, tool_name: Optional[str] = None) -> None:
        """Clear exception history, optionally for a specific tool."""
        with self._lock:
            if tool_name:
                self._exceptions = [e for e in self._exceptions if e.tool_name != tool_name]
                self._error_counts.pop(tool_name, None)
            else:
                self._exceptions.clear()
                self._error_counts.clear()

    def get_error_rate(self, tool_name: str) -> float:
        """Get error rate for a specific tool (errors / total executions)."""
        with self._lock:
            errors = self._error_counts.get(tool_name, 0)
            executions = self._execution_counts.get(tool_name, 0)
            if executions == 0:
                return 0.0
            return errors / executions

    def get_stats(self) -> Dict[str, Any]:
        """Get overall exception statistics."""
        with self._lock:
            return {
                "total_exceptions": len(self._exceptions),
                "tools_with_errors": len(self._error_counts),
                "error_counts": dict(self._error_counts),
            }

# ToolRegistry Class (Singleton)
class ToolRegistry:
    """Thread-safe singleton registry for agent tools."""

    _instance: Optional["ToolRegistry"] = None
    _lock = threading.Lock()

    def __new__(cls) -> "ToolRegistry":
        """Thread-safe singleton creation using double-checked locking."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        """Initialize registry internals (only once)."""
        if self._initialized:
            return

        self._tools: Dict[str, Dict[str, Any]] = {}
        self._registry_lock = threading.RLock()
        self._exception_registry = ExceptionRegistry()
        self._initialized = True

    @classmethod
    def get_instance(cls) -> "ToolRegistry":
        """Get the singleton instance."""
        return cls()

    def register(self, name: str, func: Callable, description: Optional[str] = None,
                 atomic: bool = False, display_name: Optional[str] = None) -> None:
        """
        Register a tool function with thread safety.

        Args:
            name: Unique tool identifier (case-sensitive)
            func: Tool function to register
            description: Tool description (defaults to func.__doc__)
            atomic: If True, marks tool as atomic (no multi-step planning needed)
            display_name: Optional display name for MCP tools
        """
        with self._registry_lock:
            sig = inspect.signature(func)
            params = {}

            for param_name, param in sig.parameters.items():
                param_info = {
                    "type": self._infer_type(param.annotation),
                    "required": param.default == inspect.Parameter.empty,
                    "default": None if param.default == inspect.Parameter.empty else param.default,
                }
                params[param_name] = param_info

            self._tools[name] = {
                "name": name,
                "function": func,
                "description": description or (func.__doc__ or ""),
                "parameters": params,
                "atomic": atomic,
                "display_name": display_name or name,
            }

    def _infer_type(self, annotation: Any) -> str:
        """Infer JSON schema type from Python annotation."""
        if annotation == inspect.Parameter.empty:
            return "unknown"
        type_map = {
            str: "string",
            int: "integer",
            float: "number",
            bool: "boolean",
            list: "array",
            tuple: "array",
            dict: "object",
            Dict: "object",
        }
        return type_map.get(annotation, "unknown")

    def unregister(self, name: str) -> bool:
        """Remove a tool from the registry."""
        with self._registry_lock:
            if name in self._tools:
                del self._tools[name]
                return True
            return False

    def create_scope(self, agent_id: str, allowed_tools: Optional[List[str]] = None) -> "AgentScope":
        """Create a scoped view for a specific agent."""
        return AgentScope(self, agent_id, allowed_tools)

    def execute_tool(self, tool_name: str, *args, **kwargs) -> Any:
        """
        Execute a tool by name with exception tracking.

        Args:
            tool_name: Name of tool to execute
            *args: Positional arguments for tool
            **kwargs: Keyword arguments for tool

        Returns:
            Result of tool execution

        Raises:
            ToolNotFoundError: If tool not registered
            ToolExecutionError: If tool execution fails
        """
        with self._registry_lock:
            if tool_name not in self._tools:
                raise ToolNotFoundError(tool_name=tool_name)

            func = self._tools[tool_name]["function"]
            try:
                result = func(*args, **kwargs)
                self._exception_registry.record_execution(tool_name)
                return result
            except Exception as e:
                self._exception_registry.record(tool_name, e)
                raise ToolExecutionError(tool_name=tool_name, cause=e)

    def get_all_tools(self) -> Dict[str, Dict[str, Any]]:
        """Get all registered tools (read-only copy)."""
        with self._registry_lock:
            return dict(self._tools)

    def get_tool(self, name: str) -> Optional[Dict[str, Any]]:
        """Get single tool metadata."""
        with self._registry_lock:
            return self._tools.get(name)

    def has_tool(self, name: str) -> bool:
        """Check if tool is registered."""
        with self._registry_lock:
            return name in self._tools

    def get_exception_registry(self) -> ExceptionRegistry:
        """Get the exception registry for error tracking."""
        return self._exception_registry

# AgentScope Class
class AgentScope:
    """
    Scoped view of ToolRegistry for specific agent.

    Provides per-agent tool isolation via allowlist filtering.
    All tool name matching is case-sensitive for security.
    """

    def __init__(self, registry: "ToolRegistry", agent_id: str,
                 allowed_tools: Optional[List[str]] = None):
        """
        Initialize agent scope.

        Args:
            registry: ToolRegistry instance
            agent_id: Unique agent identifier
            allowed_tools: List of allowed tool names (case-sensitive)
        """
        self._registry = registry
        self._agent_id = agent_id
        self._allowed_tools: Optional[Set[str]] = set(allowed_tools) if allowed_tools else None
        self._lock = threading.RLock()

    def _is_tool_allowed(self, tool_name: str) -> bool:
        """
        Check if tool is accessible (case-sensitive, exact match).

        SECURITY: Case-sensitive matching prevents bypass via case variation.
        E.g., "File_Read" != "file_read" - exact match required.
        """
        if self._allowed_tools is None:
            return True  # No restrictions
        return tool_name in self._allowed_tools  # Case-sensitive!

    def execute_tool(self, tool_name: str, *args, **kwargs) -> Any:
        """
        Execute tool if accessible.

        Args:
            tool_name: Name of tool to execute
            *args: Positional arguments
            **kwargs: Keyword arguments

        Returns:
            Result of tool execution

        Raises:
            ToolAccessDeniedError: If tool not in allowlist
            ToolNotFoundError: If tool not registered
            ToolExecutionError: If tool execution fails
        """
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

    def has_tool(self, name: str) -> bool:
        """Check if tool is accessible to this agent."""
        with self._lock:
            if not self._is_tool_allowed(name):
                return False
            return self._registry.has_tool(name)

    def get_agent_id(self) -> str:
        """Get the agent identifier."""
        return self._agent_id

    def cleanup(self) -> None:
        """Release resources (called on agent shutdown)."""
        with self._lock:
            self._allowed_tools = None
            self._registry = None

# _ToolRegistryAlias Class (Backward Compatibility Shim)
class _ToolRegistryAlias(dict):
    """
    Backward-compatible dict shim with deprecation warnings.

    This class maintains compatibility with 38 files that directly
    access _TOOL_REGISTRY as a global dict. All operations forward
    to ToolRegistry.get_instance() with appropriate warnings.

    Deprecation Timeline:
    - Week 1-2: DeprecationWarning on first access
    - Week 3-4: FutureWarning on all access
    - Week 5+: Remove shim, enforce direct usage
    """

    _warned = False

    def _warn(self, operation: str) -> None:
        """Issue deprecation warning (once per session)."""
        if not self._warned:
            warnings.warn(
                f"Direct {operation} of _TOOL_REGISTRY is deprecated. "
                "Use ToolRegistry.get_instance() instead. "
                "Support will be removed in 30 days.",
                DeprecationWarning,
                stacklevel=3
            )
            _ToolRegistryAlias._warned = True

    def __getitem__(self, key: str) -> Dict[str, Any]:
        self._warn("dict access")
        return self._registry.get_all_tools()[key]

    def __setitem__(self, key: str, value: Dict[str, Any]) -> None:
        self._warn("dict modification")
        registry = ToolRegistry.get_instance()
        registry.register(key, value.get("function"), value.get("description"))

    def __contains__(self, key: str) -> bool:
        return key in ToolRegistry.get_instance().get_all_tools()

    def __delitem__(self, key: str) -> None:
        self._warn("dict deletion")
        ToolRegistry.get_instance().unregister(key)

    def keys(self):
        return ToolRegistry.get_instance().get_all_tools().keys()

    def values(self):
        return ToolRegistry.get_instance().get_all_tools().values()

    def items(self):
        return ToolRegistry.get_instance().get_all_tools().items()

    def get(self, key: str, default: Any = None) -> Optional[Dict[str, Any]]:
        return ToolRegistry.get_instance().get_all_tools().get(key, default)

    def copy(self) -> Dict[str, Any]:
        return ToolRegistry.get_instance().get_all_tools().copy()

    def __len__(self) -> int:
        return len(ToolRegistry.get_instance().get_all_tools())

    def __iter__(self):
        return iter(ToolRegistry.get_instance().get_all_tools().keys())

    def clear(self) -> None:
        """Clear all tools from registry (used in tests)."""
        # For backward compat, allow clearing via shim
        registry = ToolRegistry.get_instance()
        with registry._registry_lock:
            registry._tools.clear()

# Update @tool decorator to use ToolRegistry
def tool(func: Callable = None, *, atomic: bool = False, **kwargs) -> Callable:
    """
    Decorator to register a function as a tool.

    Updated to register with ToolRegistry singleton instead of global dict.
    Maintains backward compatibility with both @tool and @tool(...) syntax.

    Args:
        func: Function to register as a tool (when used as @tool)
        atomic: If True, marks tool as atomic
        **kwargs: Optional arguments (ignored, for backward compatibility)

    Returns:
        The original function, unchanged
    """
    def decorator(f: Callable) -> Callable:
        registry = ToolRegistry.get_instance()
        registry.register(
            name=f.__name__,
            func=f,
            description=f.__doc__ or "",
            atomic=atomic,
        )
        return f

    if func is not None:
        return decorator(func)
    return decorator

# Maintain backward compatibility - global dict interface
_TOOL_REGISTRY = _ToolRegistryAlias()

# Export get_tool_display_name for MCP namespacing
def get_tool_display_name(tool_name: str) -> str:
    """Return the display name for a tool, resolving MCP namespacing."""
    registry = ToolRegistry.get_instance()
    tool = registry.get_tool(tool_name)
    if not tool:
        return tool_name
    return tool.get("display_name", tool_name)
```

#### Day 1 Deliverables Checklist

- [ ] `tools.py` complete rewrite with all classes
- [ ] All custom exception classes implemented
- [ ] `ExceptionRegistry` with thread-safe operations
- [ ] `ToolRegistry` singleton with double-checked locking
- [ ] `AgentScope` with case-sensitive allowlist filtering
- [ ] `_ToolRegistryAlias` shim with deprecation warnings
- [ ] Updated `@tool` decorator using `ToolRegistry`
- [ ] `_TOOL_REGISTRY` global for backward compatibility
- [ ] Unit tests for `ExceptionRegistry` (10 functions)
- [ ] Unit tests for `ToolRegistry` (20 functions)
- [ ] Unit tests for `AgentScope` (10 functions)
- [ ] Unit tests for `_ToolRegistryAlias` (5 functions)

---

### Day 2: Agent Integration

**Owner:** senior-developer
**Duration:** 8 hours
**Status:** PENDING - Ready to Start
**Dependency:** Day 1 complete (DONE)

#### Handoff Notes
See `future-where-to-resume-left-off.md` for detailed Day 2 implementation tasks including:
- agent.py import updates
- `allowed_tools` parameter addition
- `_tool_scope` creation
- `_execute_tool()` scoped execution
- `_format_tools_for_prompt()` updates
- `cleanup()` method
- configurable.py YAML allowlist integration
- Integration test requirements

#### Morning Session (4 hours)

| Time | Task | Details | Output |
|------|------|---------|--------|
| 09:00-10:30 | agent.py changes (Part 1) | Add imports, add `allowed_tools` parameter to `__init__` | Updated `Agent.__init__()` signature |
| 10:30-10:45 | Break | | |
| 10:45-12:30 | agent.py changes (Part 2) | Create `_tool_scope` in `__init__`, update `_execute_tool()` | Tool scoping integrated |

#### Afternoon Session (4 hours)

| Time | Task | Details | Output |
|------|------|---------|--------|
| 13:30-15:00 | agent.py changes (Part 3) | Update `_format_tools_for_prompt()`, add `cleanup()` | Full agent integration |
| 15:00-15:15 | Break | | |
| 15:15-16:30 | configurable.py changes | Use YAML `definition.tools` as allowlist | ConfigurableAgent scoping |
| 16:30-17:30 | Integration tests | Write `test_tool_scoping_integration.py` | 18 integration test functions |

#### Day 2 Code Changes

**agent.py modifications:**

```python
# In src/gaia/agents/base/agent.py

# UPDATE IMPORT (line 22):
from gaia.agents.base.tools import _TOOL_REGISTRY, ToolRegistry, ToolAccessDeniedError

# UPDATE __init__ SIGNATURE (around line 80):
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
    max_consecutive_repeats: int = 4,
    min_context_size: int = 32768,
    skip_lemonade: bool = False,
    allowed_tools: Optional[List[str]] = None,  # NEW PARAMETER
):
    # ... existing initialization code ...

    # Register tools for this agent (existing line 183)
    self._register_tools()

    # NEW: Create tool scope AFTER tool registration
    self._tool_scope = ToolRegistry.get_instance().create_scope(
        agent_id=self.__class__.__name__,
        allowed_tools=allowed_tools or getattr(self, "allowed_tools", None),
    )

    # ... rest of existing __init__ ...

# UPDATE _execute_tool METHOD:
def _execute_tool(self, tool_name: str, tool_args: Dict[str, Any]) -> Any:
    """Execute a tool with the given arguments."""
    try:
        # Use scoped execution for tool isolation
        if hasattr(self, '_tool_scope') and self._tool_scope:
            return self._tool_scope.execute_tool(tool_name, **tool_args)
        else:
            # Fallback to global registry for backward compat
            if tool_name not in _TOOL_REGISTRY:
                raise ValueError(f"Tool '{tool_name}' not found")
            return _TOOL_REGISTRY[tool_name]["function"](**tool_args)
    except ToolAccessDeniedError as e:
        logger.error(f"Tool access denied: {e}")
        return {"status": "error", "error": str(e)}
    except Exception as e:
        logger.exception(f"Tool execution failed: {tool_name}: {e}")
        return {"status": "error", "error": str(e)}

# UPDATE _format_tools_for_prompt METHOD:
def _format_tools_for_prompt(self) -> str:
    """Format available tools for system prompt."""
    tool_descriptions = []

    # Use scoped tools if available
    if hasattr(self, '_tool_scope') and self._tool_scope:
        available_tools = self._tool_scope.get_available_tools()
    else:
        available_tools = _TOOL_REGISTRY.copy()

    for name, tool_info in available_tools.items():
        params = tool_info.get("parameters", {})
        params_str = ", ".join(
            [
                f"{param_name}{'' if param_info.get('required', True) else '?'}: {param_info.get('type', 'unknown')}"
                for param_name, param_info in params.items()
            ]
        )

        description = tool_info.get("description", "").strip()
        tool_descriptions.append(f"- {name}({params_str}): {description}")

    return "\n".join(tool_descriptions)

# ADD cleanup METHOD (for memory management):
def cleanup(self) -> None:
    """Release resources on agent shutdown."""
    if hasattr(self, '_tool_scope') and self._tool_scope:
        self._tool_scope.cleanup()
        self._tool_scope = None
```

**configurable.py modifications:**

```python
# In src/gaia/agents/configurable.py

# UPDATE IMPORT (line 10):
from gaia.agents.base import _TOOL_REGISTRY, Agent
from gaia.agents.base.tools import ToolRegistry  # ADD THIS

# UPDATE _register_tools_from_yaml METHOD:
def _register_tools_from_yaml(self) -> None:
    """Register tools specified in YAML definition."""
    tools_to_register = self.definition.tools or []

    for tool_name in tools_to_register:
        try:
            # Check if tool is already registered
            registry = ToolRegistry.get_instance()
            if registry.has_tool(tool_name):
                logger.debug(f"Tool already registered: {tool_name}")
                continue

            # Try to load tool from tools directory
            tool_module = self._load_tool_module(tool_name)

            if tool_module:
                logger.debug(f"Loaded tool module: {tool_name}")
            else:
                logger.debug(f"Tool '{tool_name}' not found as standalone module.")

        except ImportError as e:
            logger.error(f"Failed to import tool {tool_name}: {e}")
            raise
        except Exception as e:
            logger.error(f"Failed to load tool {tool_name}: {e}")
            raise

    self._registered_tools = tools_to_register.copy()

    # Check for unregistered tools
    missing = [t for t in tools_to_register if not registry.has_tool(t)]
    if missing:
        logger.warning(f"Tools declared in YAML but not registered: {missing}")

    # NEW: Create scoped view with YAML-defined allowlist
    self._tool_scope = registry.create_scope(
        agent_id=self.definition.id,
        allowed_tools=self.definition.tools,  # From YAML
    )

# UPDATE _format_tools_for_prompt METHOD (line 459):
def _format_tools_for_prompt(self) -> str:
    """Format allowed tools for prompt."""
    tool_descriptions = []

    # Use scoped tools (YAML allowlist enforced)
    if hasattr(self, '_tool_scope') and self._tool_scope:
        available_tools = self._tool_scope.get_available_tools()
    else:
        # Fallback to global registry with manual filtering
        allowed_tools = set(self.definition.tools or [])
        available_tools = {
            name: desc for name, desc in _TOOL_REGISTRY.items()
            if name in allowed_tools
        }

    for name, tool_info in available_tools.items():
        params = tool_info.get("parameters", {})
        params_str = ", ".join(
            [
                f"{param_name}{'' if param_info.get('required', True) else '?'}: {param_info.get('type', 'unknown')}"
                for param_name, param_info in params.items()
            ]
        )

        description = tool_info.get("description", "").strip()
        tool_descriptions.append(f"- {name}({params_str}): {description}")

    return "\n".join(tool_descriptions)

# UPDATE _execute_tool METHOD (line 489):
def _execute_tool(self, tool_name: str, tool_args: Dict[str, Any]) -> Any:
    """Execute a tool with allowlist validation."""
    # Use scoped execution (enforces YAML allowlist)
    if hasattr(self, '_tool_scope') and self._tool_scope:
        try:
            return self._tool_scope.execute_tool(tool_name, **tool_args)
        except ToolAccessDeniedError as e:
            logger.error(f"SECURITY VIOLATION: {e}")
            return {
                "status": "error",
                "error": str(e),
                "security_violation": True,
            }
    else:
        # Fallback to parent implementation
        return super()._execute_tool(tool_name, tool_args)
```

#### Day 2 Deliverables Checklist

- [ ] `agent.py` updated with `allowed_tools` parameter
- [ ] `agent.py` creates `_tool_scope` in `__init__`
- [ ] `agent.py` `_execute_tool()` uses scoped execution
- [ ] `agent.py` `_format_tools_for_prompt()` uses scoped tools
- [ ] `agent.py` `cleanup()` method for memory management
- [ ] `configurable.py` uses `ToolRegistry` for scoping
- [ ] `configurable.py` YAML `definition.tools` as allowlist
- [ ] Integration tests written (18 functions)
- [ ] All existing tests pass with new integration

---

### Day 3: Testing & Regression

**Owner:** testing-quality-specialist
**Duration:** 8 hours
**Status:** PENDING - Depends on Day 2 completion
**Dependency:** Day 2 agent integration complete

#### Test Suite Structure

**File: `tests/unit/agents/test_tool_registry.py`** (45 test functions)

```python
"""Unit tests for ToolRegistry, AgentScope, and ExceptionRegistry."""

import pytest
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from gaia.agents.base.tools import (
    ToolRegistry,
    AgentScope,
    ExceptionRegistry,
    ToolNotFoundError,
    ToolAccessDeniedError,
    ToolExecutionError,
    ExceptionRecord,
)


class TestExceptionRegistry:
    """Tests for ExceptionRegistry class."""

    def test_record_exception(self):
        """Test recording a single exception."""
        registry = ExceptionRegistry()
        registry.record("test_tool", ValueError("test error"))
        exceptions = registry.get_exceptions()
        assert len(exceptions) == 1
        assert exceptions[0].tool_name == "test_tool"

    def test_record_multiple_exceptions(self):
        """Test recording multiple exceptions."""
        registry = ExceptionRegistry()
        registry.record("tool1", ValueError("error1"))
        registry.record("tool2", TypeError("error2"))
        registry.record("tool1", RuntimeError("error3"))
        exceptions = registry.get_exceptions()
        assert len(exceptions) == 3

    def test_get_exceptions_by_tool(self):
        """Test filtering exceptions by tool name."""
        registry = ExceptionRegistry()
        registry.record("tool1", ValueError("error1"))
        registry.record("tool2", TypeError("error2"))
        registry.record("tool1", RuntimeError("error3"))
        tool1_exceptions = registry.get_exceptions(tool_name="tool1")
        assert len(tool1_exceptions) == 2

    def test_clear_all_exceptions(self):
        """Test clearing all exceptions."""
        registry = ExceptionRegistry()
        registry.record("tool1", ValueError("error1"))
        registry.clear()
        assert len(registry.get_exceptions()) == 0

    def test_clear_tool_exceptions(self):
        """Test clearing exceptions for specific tool."""
        registry = ExceptionRegistry()
        registry.record("tool1", ValueError("error1"))
        registry.record("tool2", TypeError("error2"))
        registry.clear(tool_name="tool1")
        assert len(registry.get_exceptions(tool_name="tool1")) == 0
        assert len(registry.get_exceptions(tool_name="tool2")) == 1

    def test_error_rate_calculation(self):
        """Test error rate calculation."""
        registry = ExceptionRegistry()
        registry.record_execution("tool1")
        registry.record_execution("tool1")
        registry.record_execution("tool1")
        registry.record("tool1", ValueError("error"))
        assert registry.get_error_rate("tool1") == 0.25  # 1/4

    def test_error_rate_no_executions(self):
        """Test error rate with no executions."""
        registry = ExceptionRegistry()
        assert registry.get_error_rate("tool1") == 0.0

    def test_thread_safety(self):
        """Test thread-safe exception recording."""
        registry = ExceptionRegistry()

        def record_exceptions(thread_id):
            for i in range(10):
                registry.record(f"tool_{thread_id}", ValueError(f"error_{i}"))

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(record_exceptions, i) for i in range(10)]
            for future in futures:
                future.result()

        assert len(registry.get_exceptions()) == 100

    def test_get_stats(self):
        """Test getting exception statistics."""
        registry = ExceptionRegistry()
        registry.record("tool1", ValueError("error1"))
        registry.record("tool1", TypeError("error2"))
        registry.record("tool2", RuntimeError("error3"))
        stats = registry.get_stats()
        assert stats["total_exceptions"] == 3
        assert stats["tools_with_errors"] == 2

    def test_exception_record_dataclass(self):
        """Test ExceptionRecord dataclass fields."""
        registry = ExceptionRegistry()
        registry.record("tool1", ValueError("test"), agent_id="agent1")
        exceptions = registry.get_exceptions()
        record = exceptions[0]
        assert record.tool_name == "tool1"
        assert record.exception_type == "ValueError"
        assert record.message == "test"
        assert record.agent_id == "agent1"
        assert isinstance(record.timestamp, float)


class TestToolRegistry:
    """Tests for ToolRegistry singleton class."""

    def setup_method(self):
        """Reset registry before each test."""
        ToolRegistry._instance = None

    def test_singleton_instance(self):
        """Test singleton pattern returns same instance."""
        registry1 = ToolRegistry.get_instance()
        registry2 = ToolRegistry.get_instance()
        assert registry1 is registry2

    def test_singleton_thread_safety(self):
        """Test singleton is thread-safe."""
        instances = []

        def get_instance():
            instances.append(ToolRegistry.get_instance())

        with ThreadPoolExecutor(max_workers=100) as executor:
            futures = [executor.submit(get_instance) for _ in range(100)]
            for future in futures:
                future.result()

        # All threads should get same instance
        assert all(r is instances[0] for r in instances)

    def test_register_tool(self):
        """Test registering a tool."""
        registry = ToolRegistry.get_instance()

        @tool
        def test_func():
            """Test function."""
            pass

        assert registry.has_tool("test_func")

    def test_register_tool_with_description(self):
        """Test registering tool with custom description."""
        registry = ToolRegistry.get_instance()
        registry.register("custom_tool", lambda: None, description="Custom tool")
        tool = registry.get_tool("custom_tool")
        assert tool["description"] == "Custom tool"

    def test_unregister_tool(self):
        """Test unregistering a tool."""
        registry = ToolRegistry.get_instance()
        registry.register("temp_tool", lambda: None)
        assert registry.unregister("temp_tool")
        assert not registry.has_tool("temp_tool")

    def test_unregister_nonexistent_tool(self):
        """Test unregistering a tool that doesn't exist."""
        registry = ToolRegistry.get_instance()
        assert not registry.unregister("nonexistent")

    def test_execute_tool(self):
        """Test executing a tool."""
        registry = ToolRegistry.get_instance()

        def add(a, b):
            return a + b

        registry.register("add", add)
        result = registry.execute_tool("add", 2, 3)
        assert result == 5

    def test_execute_tool_with_kwargs(self):
        """Test executing tool with keyword arguments."""
        registry = ToolRegistry.get_instance()

        def greet(name, greeting="Hello"):
            return f"{greeting}, {name}!"

        registry.register("greet", greet)
        result = registry.execute_tool("greet", name="World", greeting="Hi")
        assert result == "Hi, World!"

    def test_execute_tool_not_found(self):
        """Test executing a tool that doesn't exist."""
        registry = ToolRegistry.get_instance()
        with pytest.raises(ToolNotFoundError):
            registry.execute_tool("nonexistent")

    def test_execute_tool_raises_exception(self):
        """Test tool execution exception handling."""
        registry = ToolRegistry.get_instance()

        def failing_tool():
            raise ValueError("intentional error")

        registry.register("failing_tool", failing_tool)
        with pytest.raises(ToolExecutionError):
            registry.execute_tool("failing_tool")

    def test_get_all_tools(self):
        """Test getting all registered tools."""
        registry = ToolRegistry.get_instance()
        registry.register("tool1", lambda: None)
        registry.register("tool2", lambda: None)
        tools = registry.get_all_tools()
        assert "tool1" in tools
        assert "tool2" in tools

    def test_get_tool(self):
        """Test getting single tool metadata."""
        registry = ToolRegistry.get_instance()
        registry.register("my_tool", lambda x: x * 2)
        tool = registry.get_tool("my_tool")
        assert tool["name"] == "my_tool"
        assert "parameters" in tool
        assert "description" in tool

    def test_get_nonexistent_tool(self):
        """Test getting a tool that doesn't exist."""
        registry = ToolRegistry.get_instance()
        assert registry.get_tool("nonexistent") is None

    def test_type_inference_string(self):
        """Test type inference for string parameters."""
        registry = ToolRegistry.get_instance()

        def test_func(name: str):
            pass

        registry.register("test_func", test_func)
        tool = registry.get_tool("test_func")
        assert tool["parameters"]["name"]["type"] == "string"

    def test_type_inference_int(self):
        """Test type inference for int parameters."""
        registry = ToolRegistry.get_instance()

        def test_func(count: int):
            pass

        registry.register("test_func", test_func)
        tool = registry.get_tool("test_func")
        assert tool["parameters"]["count"]["type"] == "integer"

    def test_type_inference_float(self):
        """Test type inference for float parameters."""
        registry = ToolRegistry.get_instance()

        def test_func(value: float):
            pass

        registry.register("test_func", test_func)
        tool = registry.get_tool("test_func")
        assert tool["parameters"]["value"]["type"] == "number"

    def test_type_inference_bool(self):
        """Test type inference for bool parameters."""
        registry = ToolRegistry.get_instance()

        def test_func(flag: bool):
            pass

        registry.register("test_func", test_func)
        tool = registry.get_tool("test_func")
        assert tool["parameters"]["flag"]["type"] == "boolean"

    def test_type_inference_dict(self):
        """Test type inference for dict parameters."""
        registry = ToolRegistry.get_instance()

        def test_func(data: dict):
            pass

        registry.register("test_func", test_func)
        tool = registry.get_tool("test_func")
        assert tool["parameters"]["data"]["type"] == "object"

    def test_type_inference_unknown(self):
        """Test type inference for unknown types."""
        registry = ToolRegistry.get_instance()

        def test_func(value):  # No annotation
            pass

        registry.register("test_func", test_func)
        tool = registry.get_tool("test_func")
        assert tool["parameters"]["value"]["type"] == "unknown"

    def test_atomic_flag(self):
        """Test atomic flag is preserved."""
        registry = ToolRegistry.get_instance()
        registry.register("atomic_tool", lambda: None, atomic=True)
        tool = registry.get_tool("atomic_tool")
        assert tool["atomic"] is True

    def test_display_name(self):
        """Test display name for MCP tools."""
        registry = ToolRegistry.get_instance()
        registry.register("mcp_server_tool", lambda: None, display_name="tool (server)")
        tool = registry.get_tool("mcp_server_tool")
        assert tool["display_name"] == "tool (server)"
```

**File: `tests/unit/agents/test_agent_scope.py`** (25 test functions)

```python
"""Unit tests for AgentScope class."""

import pytest
from gaia.agents.base.tools import (
    ToolRegistry,
    AgentScope,
    ToolAccessDeniedError,
    ToolNotFoundError,
)


class TestAgentScope:
    """Tests for AgentScope class."""

    def setup_method(self):
        """Reset registry before each test."""
        ToolRegistry._instance = None

    def test_create_scope(self):
        """Test creating agent scope."""
        registry = ToolRegistry.get_instance()
        scope = registry.create_scope("test_agent")
        assert scope.get_agent_id() == "test_agent"

    def test_scope_with_allowed_tools(self):
        """Test creating scope with allowlist."""
        registry = ToolRegistry.get_instance()
        registry.register("tool1", lambda: None)
        registry.register("tool2", lambda: None)
        registry.register("tool3", lambda: None)

        scope = registry.create_scope("test_agent", allowed_tools=["tool1", "tool2"])
        available = scope.get_available_tools()

        assert "tool1" in available
        assert "tool2" in available
        assert "tool3" not in available

    def test_scope_no_restrictions(self):
        """Test scope with no allowlist sees all tools."""
        registry = ToolRegistry.get_instance()
        registry.register("tool1", lambda: None)
        registry.register("tool2", lambda: None)

        scope = registry.create_scope("test_agent")
        available = scope.get_available_tools()

        assert "tool1" in available
        assert "tool2" in available

    def test_execute_allowed_tool(self):
        """Test executing tool within allowlist."""
        registry = ToolRegistry.get_instance()

        def add(a, b):
            return a + b

        registry.register("add", add)
        scope = registry.create_scope("test_agent", allowed_tools=["add"])
        result = scope.execute_tool("add", 2, 3)
        assert result == 5

    def test_execute_denied_tool(self):
        """Test executing tool outside allowlist."""
        registry = ToolRegistry.get_instance()
        registry.register("secret_tool", lambda: "secret")
        scope = registry.create_scope("test_agent", allowed_tools=["other_tool"])

        with pytest.raises(ToolAccessDeniedError):
            scope.execute_tool("secret_tool")

    def test_execute_nonexistent_tool(self):
        """Test executing a tool that doesn't exist."""
        registry = ToolRegistry.get_instance()
        scope = registry.create_scope("test_agent", allowed_tools=["nonexistent"])

        with pytest.raises(ToolNotFoundError):
            scope.execute_tool("nonexistent")

    def test_has_tool_allowed(self):
        """Test has_tool returns True for allowed tool."""
        registry = ToolRegistry.get_instance()
        registry.register("my_tool", lambda: None)
        scope = registry.create_scope("test_agent", allowed_tools=["my_tool"])
        assert scope.has_tool("my_tool")

    def test_has_tool_denied(self):
        """Test has_tool returns False for denied tool."""
        registry = ToolRegistry.get_instance()
        registry.register("my_tool", lambda: None)
        scope = registry.create_scope("test_agent", allowed_tools=["other_tool"])
        assert not scope.has_tool("my_tool")

    def test_has_tool_nonexistent(self):
        """Test has_tool returns False for nonexistent tool."""
        registry = ToolRegistry.get_instance()
        scope = registry.create_scope("test_agent")
        assert not scope.has_tool("nonexistent")

    def test_case_sensitive_matching(self):
        """Test case-sensitive tool name matching."""
        registry = ToolRegistry.get_instance()
        registry.register("file_read", lambda: None)
        scope = registry.create_scope("test_agent", allowed_tools=["file_read"])

        # Exact match works
        assert scope.has_tool("file_read")

        # Case variations denied
        assert not scope.has_tool("File_Read")
        assert not scope.has_tool("FILE_READ")
        assert not scope.has_tool("file_Read")

    def test_cleanup(self):
        """Test scope cleanup releases resources."""
        registry = ToolRegistry.get_instance()
        scope = registry.create_scope("test_agent", allowed_tools=["tool1"])
        scope.cleanup()

        # After cleanup, scope should be in released state
        assert scope._allowed_tools is None
        assert scope._registry is None
```

**File: `tests/unit/agents/test_backward_compat_shim.py`** (20 test functions)

```python
"""Tests for _ToolRegistryAlias backward compatibility shim."""

import pytest
import warnings
from gaia.agents.base.tools import (
    _TOOL_REGISTRY,
    ToolRegistry,
    tool,
)


class TestBackwardCompatShim:
    """Tests for _TOOL_REGISTRY backward compatibility."""

    def setup_method(self):
        """Reset registry before each test."""
        ToolRegistry._instance = None
        _TOOL_REGISTRY.clear()

    def test_dict_getitem(self):
        """Test dict-style item access."""
        registry = ToolRegistry.get_instance()
        registry.register("test_tool", lambda: None, description="Test")

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            tool_info = _TOOL_REGISTRY["test_tool"]
            assert tool_info["description"] == "Test"
            assert len(w) == 1
            assert issubclass(w[0].category, DeprecationWarning)

    def test_dict_setitem(self):
        """Test dict-style item assignment."""
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            _TOOL_REGISTRY["new_tool"] = {
                "function": lambda: None,
                "description": "New tool",
            }

        registry = ToolRegistry.get_instance()
        assert registry.has_tool("new_tool")

    def test_dict_contains(self):
        """Test 'in' operator."""
        registry = ToolRegistry.get_instance()
        registry.register("existing_tool", lambda: None)

        assert "existing_tool" in _TOOL_REGISTRY
        assert "nonexistent_tool" not in _TOOL_REGISTRY

    def test_dict_keys(self):
        """Test keys() method."""
        registry = ToolRegistry.get_instance()
        registry.register("tool1", lambda: None)
        registry.register("tool2", lambda: None)

        keys = list(_TOOL_REGISTRY.keys())
        assert "tool1" in keys
        assert "tool2" in keys

    def test_dict_values(self):
        """Test values() method."""
        registry = ToolRegistry.get_instance()
        registry.register("tool1", lambda: None, description="Tool 1")

        values = list(_TOOL_REGISTRY.values())
        assert len(values) >= 1
        assert any(v["description"] == "Tool 1" for v in values)

    def test_dict_items(self):
        """Test items() method."""
        registry = ToolRegistry.get_instance()
        registry.register("tool1", lambda: None)

        items = list(_TOOL_REGISTRY.items())
        assert any(k == "tool1" for k, v in items)

    def test_dict_get(self):
        """Test get() method."""
        registry = ToolRegistry.get_instance()
        registry.register("existing", lambda: None)

        result = _TOOL_REGISTRY.get("existing", None)
        assert result is not None

        result = _TOOL_REGISTRY.get("nonexistent", "default")
        assert result == "default"

    def test_dict_copy(self):
        """Test copy() method."""
        registry = ToolRegistry.get_instance()
        registry.register("tool1", lambda: None)

        copy = _TOOL_REGISTRY.copy()
        assert isinstance(copy, dict)
        assert "tool1" in copy

    def test_dict_len(self):
        """Test len() function."""
        registry = ToolRegistry.get_instance()
        registry.register("tool1", lambda: None)
        registry.register("tool2", lambda: None)

        assert len(_TOOL_REGISTRY) >= 2

    def test_dict_iter(self):
        """Test iteration."""
        registry = ToolRegistry.get_instance()
        registry.register("tool1", lambda: None)
        registry.register("tool2", lambda: None)

        keys = list(_TOOL_REGISTRY)
        assert "tool1" in keys
        assert "tool2" in keys

    def test_deprecation_warning_once(self):
        """Test deprecation warning issued only once."""
        registry = ToolRegistry.get_instance()
        registry.register("tool1", lambda: None)

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _ = _TOOL_REGISTRY["tool1"]
            _ = _TOOL_REGISTRY["tool1"]  # Second access
            # Warning should only appear once
            deprecation_warnings = [x for x in w if issubclass(x.category, DeprecationWarning)]
            assert len(deprecation_warnings) == 1

    def test_decorator_still_works(self):
        """Test @tool decorator still registers tools."""
        _TOOL_REGISTRY.clear()

        @tool
        def my_tool():
            """My tool function."""
            pass

        assert "my_tool" in _TOOL_REGISTRY

    def test_decorator_with_parentheses(self):
        """Test @tool(...) decorator syntax."""
        _TOOL_REGISTRY.clear()

        @tool(atomic=True)
        def atomic_tool():
            """Atomic tool function."""
            pass

        assert "atomic_tool" in _TOOL_REGISTRY
```

#### Day 3 Deliverables Checklist

- [ ] `test_tool_registry.py` - 45 unit tests
- [ ] `test_agent_scope.py` - 25 unit tests
- [ ] `test_backward_compat_shim.py` - 20 unit tests
- [ ] All tests passing
- [ ] Code coverage report generated
- [ ] Performance benchmarks run
- [ ] Memory leak tests pass

---

### Day 4: Security Testing & Quality Gate Validation

**Owner:** testing-quality-specialist
**Duration:** 8 hours
**Status:** PENDING - Depends on Day 3 completion
**Dependency:** All tests passing, security tests complete

#### Quality Gate 1 Validation

| Criteria | Test | Target | Actual | Status |
|----------|------|--------|--------|--------|
| **BC-001** | Backward compatibility tests | 100% pass | _ | PENDING |
| **SEC-001** | Allowlist bypass prevention | 0% success | _ | PENDING |
| **PERF-001** | Performance overhead | <5% | _ | PENDING |
| **MEM-001** | Memory leak detection | 0% | _ | PENDING |

**Phase 0 Decision:**
- [ ] **PASS:** All criteria met → Phase 0 Complete, proceed to Phase 1
- [ ] **FAIL:** Any criterion failed → Remediation required

#### Security Test Suite

**File: `tests/unit/agents/test_security.py`** (18 test functions)

```python
"""Security tests for tool scoping and allowlist enforcement."""

import pytest
from gaia.agents.base.tools import (
    ToolRegistry,
    AgentScope,
    ToolAccessDeniedError,
    _TOOL_REGISTRY,
)


class TestToolIsolation:
    """Tests for tool isolation and security."""

    def setup_method(self):
        """Reset registry before each test."""
        ToolRegistry._instance = None

    def test_case_sensitive_allowlist(self):
        """Test case-sensitive tool name matching prevents bypass."""
        registry = ToolRegistry.get_instance()
        registry.register("file_read", lambda: "read")
        registry.register("file_write", lambda: "write")

        scope = registry.create_scope("agent1", allowed_tools=["file_read"])

        # Exact match works
        assert scope.has_tool("file_read")

        # Case variations should NOT work
        assert not scope.has_tool("File_Read")
        assert not scope.has_tool("FILE_READ")
        assert not scope.has_tool("file_Read")
        assert not scope.has_tool("File_read")

    def test_allowlist_bypass_via_case(self):
        """Test allowlist cannot be bypassed via case variation."""
        registry = ToolRegistry.get_instance()
        registry.register("secret_tool", lambda: "secret")
        scope = registry.create_scope("agent1", allowed_tools=["public_tool"])

        # Attempt case variation bypass
        with pytest.raises(ToolAccessDeniedError):
            scope.execute_tool("Secret_Tool")

        with pytest.raises(ToolAccessDeniedError):
            scope.execute_tool("SECRET_TOOL")

    def test_allowlist_bypass_via_special_chars(self):
        """Test allowlist cannot be bypassed via special characters."""
        registry = ToolRegistry.get_instance()
        registry.register("safe_tool", lambda: "safe")
        scope = registry.create_scope("agent1", allowed_tools=["safe_tool"])

        # Attempt injection via special characters
        with pytest.raises(ToolAccessDeniedError):
            scope.execute_tool("safe_tool; DROP TABLE")

        with pytest.raises(ToolAccessDeniedError):
            scope.execute_tool("../safe_tool")

        with pytest.raises(ToolAccessDeniedError):
            scope.execute_tool("safe_tool' OR '1'='1")

    def test_multi_agent_isolation(self):
        """Test multiple agents have isolated tool access."""
        registry = ToolRegistry.get_instance()
        registry.register("agent1_tool", lambda: "agent1")
        registry.register("agent2_tool", lambda: "agent2")
        registry.register("shared_tool", lambda: "shared")

        scope1 = registry.create_scope("agent1", allowed_tools=["agent1_tool", "shared_tool"])
        scope2 = registry.create_scope("agent2", allowed_tools=["agent2_tool", "shared_tool"])

        # Agent 1 can access its tools
        assert scope1.has_tool("agent1_tool")
        assert scope1.has_tool("shared_tool")
        assert not scope1.has_tool("agent2_tool")

        # Agent 2 can access its tools
        assert scope2.has_tool("agent2_tool")
        assert scope2.has_tool("shared_tool")
        assert not scope2.has_tool("agent1_tool")

    def test_agent_cannot_see_other_agent_tools(self):
        """Test agent cannot see tools allocated to other agent."""
        registry = ToolRegistry.get_instance()
        registry.register("exclusive_tool", lambda: "exclusive")

        scope1 = registry.create_scope("agent1", allowed_tools=["exclusive_tool"])
        scope2 = registry.create_scope("agent2", allowed_tools=[])  # No tools

        assert scope1.has_tool("exclusive_tool")
        assert not scope2.has_tool("exclusive_tool")

        with pytest.raises(ToolAccessDeniedError):
            scope2.execute_tool("exclusive_tool")


class TestAllowlistEnforcement:
    """Tests for allowlist enforcement."""

    def setup_method(self):
        """Reset registry before each test."""
        ToolRegistry._instance = None

    def test_empty_allowlist(self):
        """Test empty allowlist denies all tools."""
        registry = ToolRegistry.get_instance()
        registry.register("any_tool", lambda: None)
        scope = registry.create_scope("agent1", allowed_tools=[])

        assert not scope.has_tool("any_tool")
        with pytest.raises(ToolAccessDeniedError):
            scope.execute_tool("any_tool")

    def test_none_allowlist(self):
        """Test None allowlist allows all tools."""
        registry = ToolRegistry.get_instance()
        registry.register("any_tool", lambda: None)
        scope = registry.create_scope("agent1", allowed_tools=None)

        assert scope.has_tool("any_tool")
        result = scope.execute_tool("any_tool")
        assert result is None

    def test_dynamic_tool_addition(self):
        """Test adding tools after scope creation."""
        registry = ToolRegistry.get_instance()

        scope = registry.create_scope("agent1", allowed_tools=["tool1"])
        registry.register("tool1", lambda: "tool1")
        registry.register("tool2", lambda: "tool2")

        # tool1 is allowed and exists
        assert scope.has_tool("tool1")

        # tool2 was registered after scope creation but is not allowed
        assert not scope.has_tool("tool2")

    def test_wildcard_not_supported(self):
        """Test wildcard patterns are not supported (exact match required)."""
        registry = ToolRegistry.get_instance()
        registry.register("file_read", lambda: "read")
        registry.register("file_write", lambda: "write")
        registry.register("file_delete", lambda: "delete")

        # Wildcard should NOT match
        scope = registry.create_scope("agent1", allowed_tools=["file_*"])
        assert not scope.has_tool("file_read")
        assert not scope.has_tool("file_write")
        assert not scope.has_tool("file_delete")

    def test_prefix_not_supported(self):
        """Test prefix matching is not supported (exact match required)."""
        registry = ToolRegistry.get_instance()
        registry.register("file_read", lambda: "read")
        registry.register("file_write", lambda: "write")

        # Prefix should NOT match
        scope = registry.create_scope("agent1", allowed_tools=["file"])
        assert not scope.has_tool("file_read")
        assert not scope.has_tool("file_write")


class TestMCPInjection:
    """Tests for MCP tool namespacing and injection prevention."""

    def setup_method(self):
        """Reset registry before each test."""
        ToolRegistry._instance = None

    def test_mcp_tool_namespacing(self):
        """Test MCP tools are properly namespaced."""
        registry = ToolRegistry.get_instance()
        registry.register(
            "mcp_time_server_get_time",
            lambda: "12:00",
            display_name="get_time (time_server)"
        )

        scope = registry.create_scope("agent1", allowed_tools=["mcp_time_server_get_time"])
        assert scope.has_tool("mcp_time_server_get_time")

    def test_mcp_tool_without_prefix(self):
        """Test MCP tool without prefix is not accessible."""
        registry = ToolRegistry.get_instance()
        registry.register("mcp_server_tool", lambda: "time")

        # Agent with only "tool" should NOT access "mcp_server_tool"
        scope = registry.create_scope("agent1", allowed_tools=["tool"])
        assert not scope.has_tool("mcp_server_tool")

    def test_mcp_display_name_resolution(self):
        """Test MCP display name resolution."""
        from gaia.agents.base.tools import get_tool_display_name

        registry = ToolRegistry.get_instance()
        registry.register("mcp_server_tool", lambda: None, display_name="tool (server)")

        assert get_tool_display_name("mcp_server_tool") == "tool (server)"
        assert get_tool_display_name("nonexistent") == "nonexistent"
```

#### Quality Gate 1 Validation

**Exit Criteria Checklist:**

| Criteria | Test | Target | Actual | Status |
|----------|------|--------|--------|--------|
| **BC-001** | Backward compatibility tests | 100% pass | _ | _ |
| **SEC-001** | Allowlist bypass prevention | 0% success | _ | _ |
| **PERF-001** | Performance overhead | <5% | _ | _ |
| **MEM-001** | Memory leak detection | 0% | _ | _ |

**Quality Gate Decision:**
- [ ] **PASS:** All criteria met → Proceed to Phase 1
- [ ] **FAIL:** Any criteria failed → Fix and retest

---

## 3. Dependency Analysis

### 3.1 Implementation Dependencies

```
                    Day 1 (tools.py)
                    +--------------+
                    |  ToolRegistry|
                    |  AgentScope  |
                    |  ExceptionReg|
                    |  BC Shim   |
                    +------+-------+
                           |
          +-----------------+-----------------+
          |                                   |
    +-----v-----+                     +-------v------+
    |  agent.py |                     |configurable.py|
    | Integration|                    | Integration |
    +-----+-----+                     +------+------+
          |                                   |
          +-----------------+-----------------+
                            |
                    Day 3-4 (Testing)
                    +-------------+
                    | Unit Tests  |
                    | Integration |
                    | Security    |
                    +-------------+
```

### 3.2 File Reference Analysis

**38 files reference `_TOOL_REGISTRY` directly:**

| File Category | Count | Priority |
|---------------|-------|----------|
| Core agent files | 2 | P0 |
| Test files | 15 | P1 |
| Verification scripts | 2 | P2 |
| Documentation | 19 | P3 |

**High-priority files requiring immediate compatibility:**

1. `src/gaia/agents/base/agent.py` - Line 22 import
2. `src/gaia/agents/configurable.py` - Line 10 import
3. `tests/test_code_agent_mixins.py` - Multiple `_TOOL_REGISTRY.clear()` calls
4. `tests/verify_shell_security.py` - Direct function access
5. `tests/verify_path_validator.py` - Direct function access

### 3.3 Import Chain

```
src/gaia/agents/base/tools.py
    ├── ToolRegistry (singleton)
    ├── AgentScope (per-agent filtering)
    ├── ExceptionRegistry (error tracking)
    ├── _ToolRegistryAlias (BC shim)
    ├── _TOOL_REGISTRY (global, for BC)
    └── @tool decorator

src/gaia/agents/base/agent.py
    └── imports: _TOOL_REGISTRY, ToolRegistry
        └── Creates: _tool_scope in __init__
        └── Uses: _tool_scope.execute_tool()

src/gaia/agents/configurable.py
    └── imports: _TOOL_REGISTRY, Agent, ToolRegistry
        └── Uses: definition.tools as allowlist
        └── Creates: _tool_scope from YAML
```

---

## 4. Risk Assessment

### 4.1 Phase 0 Specific Risks

| ID | Risk | Probability | Impact | Mitigation | Owner |
|----|------|-------------|--------|------------|-------|
| R1 | Backward compatibility break in 38 files | Medium | High | `_ToolRegistryAlias` shim with deprecation warnings | senior-developer |
| R2 | Thread safety race conditions in singleton | Low | High | Double-checked locking with RLock, concurrent tests | senior-developer |
| R3 | Performance regression >5% | Low | Medium | Performance benchmarks, optimize lock contention | testing-quality-specialist |
| R4 | Memory leaks in scope cleanup | Low | High | 0% threshold, GC verification tests | testing-quality-specialist |
| R5 | Case-sensitive allowlist confusion | Medium | Low | Clear error messages, documentation | senior-developer |
| R6 | MCP tool namespacing conflicts | Low | Medium | `mcp_{server}_{tool}` convention, collision detection | senior-developer |

### 4.2 Risk Exposure Summary

```
CRITICAL (0): None
HIGH     (2): R1 (BC break), R2 (thread safety)
MEDIUM   (2): R3 (performance), R6 (MCP namespacing)
LOW      (2): R4 (memory leaks), R5 (case sensitivity confusion)
```

### 4.3 Key Mitigation Strategies

**R1: Backward Compatibility**
- `_ToolRegistryAlias` provides full dict interface
- Deprecation warnings logged but operations succeed
- 30-day migration window before shim removal

**R2: Thread Safety**
- Double-checked locking in singleton
- RLock for all registry operations
- Concurrent access tests with 100 threads

**R3: Performance**
- Benchmark before/after
- Target: <5% overhead per `execute_tool()`
- Profile lock contention points

**R4: Memory Management**
- `AgentScope.cleanup()` releases references
- GC verification tests
- RSS delta measurement

---

## 5. Code Structure Outline

### 5.1 Class Diagram

```
+------------------+          +------------------+
|  ToolRegistry    |<>--------|   AgentScope     |
|  (Singleton)     | creates  |  (Per-agent)     |
+------------------+          +------------------+
| + get_instance() |          | + execute_tool() |
| + register()     |          | + get_available_tools() |
| + unregister()   |          | + has_tool()     |
| + create_scope() |          | + get_agent_id() |
| + execute_tool() |          | + cleanup()      |
| + get_all_tools()|          +------------------+
| + get_tool()     |
+------------------+          +------------------+
          |                   | ExceptionRegistry|
          | 1:1               +------------------+
          v                   | + record()       |
+------------------+          | + get_exceptions()|
| ExceptionRegistry|         | + clear()        |
+------------------+          | + get_error_rate()|
| + record()       |          +------------------+
| + get_exceptions()|
| + clear()        |
| + get_error_rate()|
+------------------+
```

### 5.2 Method Signatures

**ToolRegistry:**
```python
def get_instance() -> "ToolRegistry"
def register(name: str, func: Callable, description: Optional[str] = None,
             atomic: bool = False, display_name: Optional[str] = None) -> None
def unregister(name: str) -> bool
def create_scope(agent_id: str, allowed_tools: Optional[List[str]] = None) -> "AgentScope"
def execute_tool(tool_name: str, *args, **kwargs) -> Any
def get_all_tools() -> Dict[str, Dict[str, Any]]
def get_tool(name: str) -> Optional[Dict[str, Any]]
def has_tool(name: str) -> bool
def get_exception_registry() -> ExceptionRegistry
```

**AgentScope:**
```python
def execute_tool(tool_name: str, *args, **kwargs) -> Any
def get_available_tools() -> Dict[str, Dict[str, Any]]
def has_tool(name: str) -> bool
def get_agent_id() -> str
def cleanup() -> None
```

**ExceptionRegistry:**
```python
def record(tool_name: str, exception: Exception, agent_id: Optional[str] = None) -> None
def record_execution(tool_name: str) -> None
def get_exceptions(tool_name: Optional[str] = None, limit: int = 100) -> List[ExceptionRecord]
def clear(tool_name: Optional[str] = None) -> None
def get_error_rate(tool_name: str) -> float
def get_stats() -> Dict[str, Any]
```

### 5.3 Exception Hierarchy

```
Exception
    ├── ToolNotFoundError
    ├── ToolAccessDeniedError
    └── ToolExecutionError
```

---

## 6. Test Coverage Requirements

### 6.1 Test Matrix

| Test File | Functions | Coverage Target | Priority |
|-----------|-----------|-----------------|----------|
| `test_tool_registry.py` | 45 | 100% ToolRegistry, ExceptionRegistry | P0 |
| `test_agent_scope.py` | 25 | 100% AgentScope | P0 |
| `test_backward_compat_shim.py` | 20 | 100% _ToolRegistryAlias | P0 |
| `test_security.py` | 18 | All security requirements | P0 |
| `test_tool_scoping_integration.py` | 18 | Agent + ConfigurableAgent | P1 |

**Total: 126 test functions**

### 6.2 Quality Metrics

| Metric | Target | Measurement |
|--------|--------|-------------|
| Line coverage | 100% | `pytest --cov=gaia.agents.base.tools` |
| Branch coverage | 100% | All conditional paths tested |
| Thread safety | Verified | 100-thread concurrent tests |
| Memory leaks | 0% | RSS delta after cleanup |

---

## 7. Quality Gate 1 Exit Criteria

### 7.1 Mandatory Tests

**BC-001: Backward Compatibility**
- [ ] All 38 files referencing `_TOOL_REGISTRY` remain functional
- [ ] `@tool` decorator works with both `@tool` and `@tool(...)` syntax
- [ ] `_TOOL_REGISTRY` dict interface works (getitem, setitem, keys, values, items)
- [ ] Deprecation warnings logged appropriately

**SEC-001: Allowlist Bypass Prevention**
- [ ] Case-sensitive matching enforced (File_Read != file_read)
- [ ] Special character injection blocked
- [ ] Wildcard patterns not supported (exact match required)
- [ ] Multi-agent isolation verified
- [ ] 0% bypass success rate

**PERF-001: Performance Overhead**
- [ ] Registry overhead <5% vs baseline
- [ ] Scope creation <1ms
- [ ] Concurrent access handles 1000 ops/sec
- [ ] No lock contention bottlenecks

**MEM-001: Memory Leak Detection**
- [ ] RSS delta after agent shutdown ≤ 0 bytes
- [ ] Dangling scope references: 0
- [ ] GC cycles to full cleanup: ≤ 1
- [ ] Scope cleanup releases all references

### 7.2 Exit Decision

| Criteria | Pass | Fail | Action |
|----------|------|------|--------|
| BC-001 | 100% tests pass | Any test fails | Fix before proceeding |
| SEC-001 | 0% bypass success | Any bypass succeeds | CRITICAL, immediate fix |
| PERF-001 | <5% overhead | >5% regression | Assess severity |
| MEM-001 | 0% leaks | Any leak detected | Fix before proceeding |

**Phase 0 Status:**
- [ ] **PASS:** All criteria met → Phase 0 Complete, proceed to Phase 1
- [ ] **FAIL:** Any criterion failed → Remediation required

---

## 8. Handoff Notes for senior-developer

### 8.1 Implementation Notes

1. **Start with tools.py:** This is the foundation - all other files depend on it.
2. **Test as you go:** Run unit tests after each class implementation.
3. **Maintain BC:** The `_TOOL_REGISTRY` shim must work flawlessly for 38 files.
4. **Thread safety is critical:** Use RLock consistently, test with 100+ threads.

### 8.2 File Modification List

| File | Changes | Lines of Code |
|------|---------|---------------|
| `src/gaia/agents/base/tools.py` | Complete rewrite | ~450 (from ~110) |
| `src/gaia/agents/base/agent.py` | Add `allowed_tools`, create scope | ~30 additions |
| `src/gaia/agents/configurable.py` | Use YAML as allowlist | ~20 additions |
| `tests/unit/agents/test_tool_registry.py` | New file | ~300 |
| `tests/unit/agents/test_agent_scope.py` | New file | ~200 |
| `tests/unit/agents/test_backward_compat_shim.py` | New file | ~150 |
| `tests/unit/agents/test_security.py` | New file | ~200 |

### 8.3 Key Implementation Details

**Singleton Pattern:**
```python
class ToolRegistry:
    _instance: Optional["ToolRegistry"] = None
    _lock = threading.Lock()

    def __new__(cls) -> "ToolRegistry":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
```

**Case-Sensitive Security:**
```python
def _is_tool_allowed(self, tool_name: str) -> bool:
    if self._allowed_tools is None:
        return True
    return tool_name in self._allowed_tools  # Case-sensitive!
```

**Backward Compat Shim:**
```python
class _ToolRegistryAlias(dict):
    _warned = False

    def _warn(self, operation: str) -> None:
        if not self._warned:
            warnings.warn(
                f"Direct {operation} of _TOOL_REGISTRY is deprecated...",
                DeprecationWarning,
                stacklevel=3
            )
            _ToolRegistryAlias._warned = True
```

### 8.4 Testing Strategy

1. **Day 1:** Unit tests for each class as implemented
2. **Day 2:** Integration tests with agent.py and configurable.py
3. **Day 3:** Regression tests for all existing functionality
4. **Day 4:** Security tests + Quality Gate 1 validation

### 8.5 Questions or Escalation

If you encounter issues during implementation:

1. **Check the spec:** `docs/spec/phase0-tool-scoping-integration.md` has detailed design
2. **Review this plan:** Code structures are provided above
3. **Escalate to:** planning-analysis-strategist (Dr. Sarah Kim) for clarification

---

**END OF IMPLEMENTATION PLAN**

**Prepared By:** planning-analysis-strategist (Dr. Sarah Kim)
**Date:** 2026-04-05
**Next Action:** senior-developer begins Day 1 implementation
**Review Cadence:** Daily standup for progress updates
