# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Tool registry and decorator for agent tools.

This module provides a thread-safe singleton registry for agent tools with
per-agent scoping and exception tracking. It maintains backward compatibility
with the legacy global dict interface while providing enhanced security and
isolation features.

Key Components:
    - ToolRegistry: Thread-safe singleton for tool registration and execution
    - AgentScope: Per-agent scoped view with allowlist filtering
    - ExceptionRegistry: Tracks tool execution exceptions for error analysis
    - _ToolRegistryAlias: Backward-compatible dict shim with deprecation warnings

Example Usage:
    ```python
    from gaia.agents.base.tools import ToolRegistry, tool

    # Using the @tool decorator
    @tool
    def my_tool(param: str) -> str:
        \"\"\"My tool description.\"\"\"
        return f"Processed: {param}"

    # Using the singleton registry
    registry = ToolRegistry.get_instance()
    scope = registry.create_scope("agent1", allowed_tools=["my_tool"])
    result = scope.execute_tool("my_tool", "input")
    ```
"""

import inspect
import logging
import threading
import time
import warnings
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set, Type

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# =============================================================================
# Custom Exceptions
# =============================================================================

class ToolNotFoundError(Exception):
    """Raised when attempting to execute an unregistered tool.

    This exception indicates that the requested tool name does not exist
    in the registry. The agent should handle this gracefully and inform
    the user that the requested tool is unavailable.

    Attributes:
        tool_name: The name of the tool that was not found.
    """

    def __init__(self, tool_name: str):
        self.tool_name = tool_name
        super().__init__(f"Tool '{tool_name}' not found in registry")


class ToolAccessDeniedError(Exception):
    """Raised when agent attempts to access a tool outside its allowlist.

    This exception indicates a security violation where an agent attempted
    to execute a tool that is not in its permitted tool list. This is a
    security enforcement mechanism to prevent unauthorized tool access.

    Attributes:
        tool_name: The name of the tool that was denied.
        agent_id: The identifier of the agent that was denied access.
    """

    def __init__(self, tool_name: str, agent_id: str):
        self.tool_name = tool_name
        self.agent_id = agent_id
        super().__init__(
            f"Agent '{agent_id}' denied access to tool '{tool_name}'"
        )


class ToolExecutionError(Exception):
    """Raised when tool execution fails.

    This exception wraps the original exception raised during tool execution,
    providing context about which tool failed. The original exception is
    preserved in the `cause` attribute for debugging purposes.

    Attributes:
        tool_name: The name of the tool that failed.
        cause: The original exception that caused the failure.
    """

    def __init__(self, tool_name: str, cause: Exception):
        self.tool_name = tool_name
        self.cause = cause
        super().__init__(f"Tool '{tool_name}' execution failed: {cause}")


# =============================================================================
# Exception Record Dataclass
# =============================================================================

@dataclass
class ExceptionRecord:
    """Record of a tool execution exception.

    This dataclass captures metadata about exceptions that occur during
    tool execution. The records are stored in ExceptionRegistry for
    later analysis and debugging.

    Attributes:
        tool_name: Name of the tool that raised the exception.
        exception_type: Class name of the exception (e.g., 'ValueError').
        message: The exception message string.
        traceback: Optional stack trace (can be populated from sys.exc_info()).
        timestamp: Unix timestamp when the exception occurred.
        agent_id: Optional identifier of the agent that triggered the tool.
    """
    tool_name: str
    exception_type: str
    message: str
    traceback: str
    timestamp: float
    agent_id: Optional[str] = None


# =============================================================================
# ExceptionRegistry Class
# =============================================================================

class ExceptionRegistry:
    """Thread-safe registry for tracking tool execution exceptions.

    This class provides centralized exception tracking for all tool executions.
    It records exceptions with metadata, tracks error rates per tool, and
    supports filtering and statistics for debugging and monitoring purposes.

    Thread Safety:
        All public methods are thread-safe using RLock for reentrant locking.

    Example Usage:
        ```python
        registry = ExceptionRegistry()
        registry.record("my_tool", ValueError("test error"), agent_id="agent1")
        exceptions = registry.get_exceptions(tool_name="my_tool")
        error_rate = registry.get_error_rate("my_tool")
        ```
    """

    def __init__(self):
        """Initialize the exception registry with empty storage."""
        self._exceptions: List[ExceptionRecord] = []
        self._lock = threading.RLock()
        self._error_counts: Dict[str, int] = {}
        self._execution_counts: Dict[str, int] = {}

    def record(
        self,
        tool_name: str,
        exception: Exception,
        agent_id: Optional[str] = None
    ) -> None:
        """Record an exception that occurred during tool execution.

        This method captures exception metadata including type, message,
        and timestamp. It also increments the error count for the tool
        for error rate calculation.

        Args:
            tool_name: Name of the tool that raised the exception.
            exception: The exception instance that was raised.
            agent_id: Optional identifier of the agent that triggered the tool.
        """
        with self._lock:
            record = ExceptionRecord(
                tool_name=tool_name,
                exception_type=type(exception).__name__,
                message=str(exception),
                traceback="",  # Can be populated from sys.exc_info() if needed
                timestamp=time.time(),
                agent_id=agent_id,
            )
            self._exceptions.append(record)
            self._error_counts[tool_name] = self._error_counts.get(tool_name, 0) + 1

    def record_execution(self, tool_name: str) -> None:
        """Record a successful tool execution for error rate calculation.

        This method should be called after each successful tool execution
        to maintain accurate error rate statistics.

        Args:
            tool_name: Name of the tool that was executed successfully.
        """
        with self._lock:
            self._execution_counts[tool_name] = self._execution_counts.get(tool_name, 0) + 1

    def get_exceptions(
        self,
        tool_name: Optional[str] = None,
        limit: int = 100
    ) -> List[ExceptionRecord]:
        """Get recorded exceptions, optionally filtered by tool name.

        Args:
            tool_name: Optional tool name to filter exceptions. If None,
                returns all exceptions.
            limit: Maximum number of exceptions to return (default 100).

        Returns:
            List of ExceptionRecord objects matching the filter criteria.
        """
        with self._lock:
            if tool_name:
                return [e for e in self._exceptions if e.tool_name == tool_name][:limit]
            return self._exceptions[-limit:]

    def clear(self, tool_name: Optional[str] = None) -> None:
        """Clear exception history, optionally for a specific tool.

        Args:
            tool_name: Optional tool name to clear exceptions for.
                If None, clears all exception history.
        """
        with self._lock:
            if tool_name:
                self._exceptions = [
                    e for e in self._exceptions if e.tool_name != tool_name
                ]
                self._error_counts.pop(tool_name, None)
            else:
                self._exceptions.clear()
                self._error_counts.clear()
                self._execution_counts.clear()

    def get_error_rate(self, tool_name: str) -> float:
        """Get error rate for a specific tool (errors / total executions).

        Args:
            tool_name: Name of the tool to calculate error rate for.

        Returns:
            Error rate as a float between 0.0 and 1.0. Returns 0.0 if
            there have been no executions.
        """
        with self._lock:
            errors = self._error_counts.get(tool_name, 0)
            executions = self._execution_counts.get(tool_name, 0)
            if executions == 0:
                return 0.0
            return errors / executions

    def get_stats(self) -> Dict[str, Any]:
        """Get overall exception statistics.

        Returns:
            Dictionary containing:
            - total_exceptions: Total number of recorded exceptions
            - tools_with_errors: Number of distinct tools with errors
            - error_counts: Dictionary mapping tool names to error counts
        """
        with self._lock:
            return {
                "total_exceptions": len(self._exceptions),
                "tools_with_errors": len(self._error_counts),
                "error_counts": dict(self._error_counts),
            }


# =============================================================================
# ToolRegistry Class (Singleton)
# =============================================================================

class ToolRegistry:
    """Thread-safe singleton registry for agent tools.

    This class provides centralized tool registration, discovery, and execution
    with full thread safety. It uses the singleton pattern with double-checked
    locking to ensure only one registry instance exists across the application.

    Features:
        - Thread-safe singleton with double-checked locking
        - Tool registration with automatic parameter type inference
        - Scoped tool execution with per-agent allowlists
        - Integrated exception tracking via ExceptionRegistry

    Thread Safety:
        All public methods are thread-safe. The singleton uses double-checked
        locking for instance creation, and all registry operations use an RLock
        to prevent race conditions.

    Example Usage:
        ```python
        from gaia.agents.base.tools import ToolRegistry

        # Get singleton instance
        registry = ToolRegistry.get_instance()

        # Register a tool
        def my_tool(x: int) -> int:
            return x * 2

        registry.register("my_tool", my_tool, description="Doubles the input")

        # Create a scoped view for an agent
        scope = registry.create_scope("agent1", allowed_tools=["my_tool"])

        # Execute tool through scope
        result = scope.execute_tool("my_tool", 5)  # Returns 10
        ```
    """

    _instance: Optional["ToolRegistry"] = None
    _lock = threading.Lock()

    def __new__(cls) -> "ToolRegistry":
        """Create singleton instance using double-checked locking.

        This method ensures thread-safe singleton creation. The outer check
        avoids unnecessary lock acquisition, while the inner check prevents
        multiple instances in concurrent scenarios.

        Returns:
            The singleton ToolRegistry instance.
        """
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        """Initialize registry internals (only once per singleton).

        This method initializes the internal tool storage and exception
        registry. The _initialized flag ensures initialization only runs
        once, even if __init__ is called multiple times on the singleton.
        """
        if self._initialized:
            return

        self._tools: Dict[str, Dict[str, Any]] = {}
        self._registry_lock = threading.RLock()
        self._exception_registry = ExceptionRegistry()
        self._initialized = True

    @classmethod
    def get_instance(cls) -> "ToolRegistry":
        """Get the singleton instance.

        Returns:
            The singleton ToolRegistry instance.
        """
        return cls()

    def register(
        self,
        name: str,
        func: Callable,
        description: Optional[str] = None,
        atomic: bool = False,
        display_name: Optional[str] = None
    ) -> None:
        """Register a tool function with thread safety.

        This method registers a callable as a tool in the registry. It extracts
        parameter information from the function signature and infers JSON schema
        types from Python type annotations.

        Args:
            name: Unique tool identifier (case-sensitive).
            func: The tool function to register.
            description: Tool description (defaults to func.__doc__).
            atomic: If True, marks tool as atomic (no multi-step planning needed).
            display_name: Optional display name for MCP tools (e.g., "tool (server)").
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
        """Infer JSON schema type from Python type annotation.

        This helper method maps Python types to their JSON schema equivalents
        for tool parameter documentation.

        Args:
            annotation: Python type annotation (e.g., str, int, float, dict).

        Returns:
            JSON schema type string ("string", "integer", "number",
            "boolean", "array", "object", or "unknown").
        """
        if annotation == inspect.Parameter.empty:
            return "unknown"

        type_map: Dict[Type, str] = {
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
        """Remove a tool from the registry.

        Args:
            name: The tool name to remove.

        Returns:
            True if tool was removed, False if tool was not found.
        """
        with self._registry_lock:
            if name in self._tools:
                del self._tools[name]
                return True
            return False

    def create_scope(
        self,
        agent_id: str,
        allowed_tools: Optional[List[str]] = None
    ) -> "AgentScope":
        """Create a scoped view for a specific agent.

        This method creates an AgentScope instance that provides filtered
        access to tools based on the provided allowlist.

        Args:
            agent_id: Unique identifier for the agent.
            allowed_tools: List of allowed tool names (case-sensitive).
                If None, the agent has access to all registered tools.

        Returns:
            An AgentScope instance configured for the agent.
        """
        return AgentScope(self, agent_id, allowed_tools)

    def execute_tool(self, tool_name: str, *args, **kwargs) -> Any:
        """Execute a tool by name with exception tracking.

        This method looks up and executes a registered tool, passing through
        all arguments. It tracks successful executions and exceptions for
        error rate calculation.

        Args:
            tool_name: Name of tool to execute.
            *args: Positional arguments to pass to the tool function.
            **kwargs: Keyword arguments to pass to the tool function.

        Returns:
            The result of the tool execution.

        Raises:
            ToolNotFoundError: If the tool is not registered.
            ToolExecutionError: If tool execution fails.
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
        """Get all registered tools as a read-only copy.

        Returns:
            Dictionary mapping tool names to their metadata dictionaries.
        """
        with self._registry_lock:
            return dict(self._tools)

    def get_tool(self, name: str) -> Optional[Dict[str, Any]]:
        """Get metadata for a single tool.

        Args:
            name: The tool name to look up.

        Returns:
            Tool metadata dictionary, or None if not found.
        """
        with self._registry_lock:
            return self._tools.get(name)

    def has_tool(self, name: str) -> bool:
        """Check if a tool is registered.

        Args:
            name: The tool name to check.

        Returns:
            True if the tool is registered, False otherwise.
        """
        with self._registry_lock:
            return name in self._tools

    def get_exception_registry(self) -> ExceptionRegistry:
        """Get the exception registry for error tracking.

        Returns:
            The ExceptionRegistry instance used for tracking tool errors.
        """
        return self._exception_registry


# =============================================================================
# AgentScope Class
# =============================================================================

class AgentScope:
    """Scoped view of ToolRegistry for a specific agent.

    This class provides per-agent tool isolation through allowlist filtering.
    All tool name matching is case-sensitive for security, preventing bypass
    attempts via case variation.

    Security Features:
        - Case-sensitive tool name matching (exact match required)
        - Allowlist-based access control
        - Clear error messages on access denied

    Thread Safety:
        All public methods are thread-safe using RLock.

    Example Usage:
        ```python
        from gaia.agents.base.tools import ToolRegistry

        registry = ToolRegistry.get_instance()

        # Create scope with limited tool access
        scope = registry.create_scope(
            "restricted_agent",
            allowed_tools=["read_file", "write_file"]
        )

        # These will succeed
        scope.execute_tool("read_file", "path/to/file")

        # This will raise ToolAccessDeniedError
        scope.execute_tool("execute_bash", "rm -rf /")
        ```
    """

    def __init__(
        self,
        registry: "ToolRegistry",
        agent_id: str,
        allowed_tools: Optional[List[str]] = None
    ):
        """Initialize agent scope with allowlist.

        Args:
            registry: The ToolRegistry instance to wrap.
            agent_id: Unique identifier for the agent.
            allowed_tools: List of allowed tool names (case-sensitive).
                If None, the agent has access to all registered tools.
        """
        self._registry = registry
        self._agent_id = agent_id
        # Convert to set, preserving empty list as empty set (not None)
        # None means no restrictions, empty set means no tools allowed
        self._allowed_tools: Optional[Set[str]] = set(allowed_tools) if allowed_tools is not None else None
        self._lock = threading.RLock()

    def _is_tool_allowed(self, tool_name: str) -> bool:
        """Check if tool is accessible (case-sensitive, exact match).

        SECURITY: Case-sensitive matching prevents bypass via case variation.
        For example, "File_Read" != "file_read" - exact match required.

        Args:
            tool_name: The tool name to check.

        Returns:
            True if the tool is allowed, False otherwise.
        """
        if self._allowed_tools is None:
            return True  # No restrictions
        return tool_name in self._allowed_tools  # Case-sensitive!

    def execute_tool(self, tool_name: str, *args, **kwargs) -> Any:
        """Execute tool if accessible.

        This method first checks if the tool is in the agent's allowlist,
        then executes through the underlying registry.

        Args:
            tool_name: Name of tool to execute.
            *args: Positional arguments for the tool.
            **kwargs: Keyword arguments for the tool.

        Returns:
            The result of tool execution.

        Raises:
            ToolAccessDeniedError: If tool not in allowlist.
            ToolNotFoundError: If tool not registered.
            ToolExecutionError: If tool execution fails.
        """
        with self._lock:
            if not self._is_tool_allowed(tool_name):
                raise ToolAccessDeniedError(
                    tool_name=tool_name,
                    agent_id=self._agent_id
                )
            return self._registry.execute_tool(tool_name, *args, **kwargs)

    def get_available_tools(self) -> Dict[str, Dict[str, Any]]:
        """Get tools accessible to this agent.

        Returns:
            Dictionary of tool metadata for tools in this agent's allowlist.
        """
        with self._lock:
            all_tools = self._registry.get_all_tools()
            if self._allowed_tools is None:
                return all_tools
            return {
                name: desc for name, desc in all_tools.items()
                if name in self._allowed_tools
            }

    def has_tool(self, name: str) -> bool:
        """Check if tool is accessible to this agent.

        This method verifies both that the tool is in the allowlist
        AND that it exists in the registry.

        Args:
            name: The tool name to check.

        Returns:
            True if the tool is accessible, False otherwise.
        """
        with self._lock:
            if not self._is_tool_allowed(name):
                return False
            return self._registry.has_tool(name)

    def get_agent_id(self) -> str:
        """Get the agent identifier.

        Returns:
            The agent ID passed during scope creation.
        """
        return self._agent_id

    def cleanup(self) -> None:
        """Release resources on agent shutdown.

        This method clears the allowlist and registry references to
        prevent memory leaks. It should be called when the agent is
        being destroyed.
        """
        with self._lock:
            self._allowed_tools = None
            self._registry = None


# =============================================================================
# _ToolRegistryAlias Class (Backward Compatibility Shim)
# =============================================================================

class _ToolRegistryAlias(dict):
    """Backward-compatible dict shim with deprecation warnings.

    This class maintains compatibility with 38 files that directly access
    _TOOL_REGISTRY as a global dict. All operations forward to the
    ToolRegistry singleton with appropriate deprecation warnings.

    Deprecation Timeline:
        - Week 1-2: DeprecationWarning on first access
        - Week 3-4: FutureWarning on all access
        - Week 5+: Remove shim, enforce direct usage

    Thread Safety:
        All operations are thread-safe as they delegate to ToolRegistry.

    Example Usage:
        ```python
        # Legacy code (still works but issues deprecation warning)
        from gaia.agents.base.tools import _TOOL_REGISTRY

        tool_info = _TOOL_REGISTRY["my_tool"]  # Works with warning
        ```
    """

    _warned = False

    def _warn(self, operation: str) -> None:
        """Issue deprecation warning (once per session).

        Args:
            operation: The type of operation being performed (e.g., "dict access").
        """
        if not self._warned:
            warnings.warn(
                f"Direct {operation} of _TOOL_REGISTRY is deprecated. "
                "Use ToolRegistry.get_instance() instead. "
                "Support will be removed in 30 days.",
                DeprecationWarning,
                stacklevel=3
            )
            _ToolRegistryAlias._warned = True

    @property
    def _registry(self) -> ToolRegistry:
        """Get the underlying ToolRegistry singleton."""
        return ToolRegistry.get_instance()

    def __getitem__(self, key: str) -> Dict[str, Any]:
        """Dict-style item access with deprecation warning."""
        self._warn("dict access")
        return self._registry.get_all_tools()[key]

    def __setitem__(self, key: str, value: Dict[str, Any]) -> None:
        """Dict-style item assignment with deprecation warning."""
        self._warn("dict modification")
        self._registry.register(
            key,
            value.get("function"),
            value.get("description")
        )

    def __contains__(self, key: str) -> bool:
        """Dict-style containment check."""
        return key in self._registry.get_all_tools()

    def __delitem__(self, key: str) -> None:
        """Dict-style item deletion with deprecation warning."""
        self._warn("dict deletion")
        self._registry.unregister(key)

    def keys(self):
        """Return dict keys view."""
        return self._registry.get_all_tools().keys()

    def values(self):
        """Return dict values view."""
        return self._registry.get_all_tools().values()

    def items(self):
        """Return dict items view."""
        return self._registry.get_all_tools().items()

    def get(self, key: str, default: Any = None) -> Optional[Dict[str, Any]]:
        """Dict-style get method."""
        return self._registry.get_all_tools().get(key, default)

    def copy(self) -> Dict[str, Any]:
        """Return a copy of the tools dictionary."""
        return self._registry.get_all_tools().copy()

    def __len__(self) -> int:
        """Return number of registered tools."""
        return len(self._registry.get_all_tools())

    def __iter__(self):
        """Iterate over tool names."""
        return iter(self._registry.get_all_tools().keys())

    def clear(self) -> None:
        """Clear all tools from registry (used primarily in tests)."""
        registry = self._registry
        with registry._registry_lock:
            registry._tools.clear()


# =============================================================================
# Public API Functions
# =============================================================================

def tool(func: Callable = None, *, atomic: bool = False, **kwargs) -> Callable:
    """Decorator to register a function as a tool.

    This decorator registers a function with the ToolRegistry singleton,
    extracting parameter information and inferring types from annotations.
    It maintains backward compatibility with both @tool and @tool(...) syntax.

    Args:
        func: Function to register as a tool (when used as @tool).
        atomic: If True, marks tool as atomic (no multi-step planning needed).
        **kwargs: Optional arguments (ignored, for backward compatibility).

    Returns:
        The original function, unchanged.

    Example Usage:
        ```python
        # Simple usage
        @tool
        def read_file(path: str) -> str:
            \"\"\"Read a file and return its contents.\"\"\"
            with open(path) as f:
                return f.read()

        # With arguments
        @tool(atomic=True)
        def write_file(path: str, content: str) -> None:
            \"\"\"Write content to a file.\"\"\"
            with open(path, "w") as f:
                f.write(content)
        ```
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


def get_tool_display_name(tool_name: str) -> str:
    """Return the display name for a tool, resolving MCP namespacing.

    MCP tools are registered under a prefixed key (``mcp_{server}_{tool}``) to
    avoid name conflicts. Their ``display_name`` field preserves the original
    tool name together with the server origin, e.g. ``"read_file (myserver)"``,
    so console output remains meaningful. Native tools carry no ``display_name``
    and are returned as-is.

    Args:
        tool_name: The internal tool name as stored in the registry
            (e.g. ``"mcp_myserver_read_file"`` or ``"read_file"``).

    Returns:
        The ``display_name`` when set (MCP tools), otherwise ``tool_name``.
    """
    registry = ToolRegistry.get_instance()
    tool = registry.get_tool(tool_name)
    if not tool:
        return tool_name
    return tool.get("display_name", tool_name)


# =============================================================================
# Global Backward Compatibility Instance
# =============================================================================

# Maintain backward compatibility - global dict interface
# This allows existing code to continue working while issuing deprecation warnings
_TOOL_REGISTRY = _ToolRegistryAlias()
