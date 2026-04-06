"""
Unit tests for AgentScope class.

This test suite validates the per-agent tool scoping functionality including:
- AgentScope creation and configuration
- Allowlist filtering and enforcement
- Case-sensitive matching (SEC-001 security requirement)
- Tool access control and isolation
- Cleanup behavior and memory management

Quality Gate 1 Criteria Covered:
- SEC-001: Allowlist bypass prevention via case-sensitive matching
- MEM-001: Memory leak detection through cleanup() verification
"""

import pytest
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from gaia.agents.base.tools import (
    ToolRegistry,
    AgentScope,
    ToolAccessDeniedError,
    ToolNotFoundError,
    ToolExecutionError,
)


# =============================================================================
# AgentScope Creation Tests
# =============================================================================

class TestAgentScopeCreation:
    """Tests for AgentScope creation and initialization."""

    def setup_method(self):
        """Reset registry and clear tools before each test."""
        ToolRegistry._instance = None
        self.registry = ToolRegistry.get_instance()
        # Clear any tools from previous tests
        with self.registry._registry_lock:
            self.registry._tools.clear()

    def teardown_method(self):
        """Reset singleton after each test."""
        ToolRegistry._instance = None

    def test_create_scope_via_registry(self):
        """Test creating agent scope via registry.create_scope()."""
        scope = self.registry.create_scope("test_agent")
        assert scope is not None
        assert isinstance(scope, AgentScope)

    def test_scope_has_agent_id(self):
        """Test scope stores and returns agent identifier."""
        scope = self.registry.create_scope("my_agent_123")
        assert scope.get_agent_id() == "my_agent_123"

    def test_scope_with_none_allowlist(self):
        """Test creating scope with None allowlist (unrestricted)."""
        scope = self.registry.create_scope("test_agent", allowed_tools=None)
        assert scope._allowed_tools is None

    def test_scope_with_empty_allowlist(self):
        """Test creating scope with empty allowlist (no tools allowed).

        An empty allowlist means NO tools are accessible - this is different
        from None which means all tools are accessible (no restrictions).
        """
        scope = self.registry.create_scope("test_agent", allowed_tools=[])
        # Empty allowlist should be preserved as empty set (not None)
        assert scope._allowed_tools == set()
        assert scope._allowed_tools is not None  # Explicitly not unrestricted

    def test_scope_with_single_tool_allowlist(self):
        """Test creating scope with single tool in allowlist."""
        scope = self.registry.create_scope("test_agent", allowed_tools=["read_file"])
        assert "read_file" in scope._allowed_tools

    def test_scope_with_multiple_tools_allowlist(self):
        """Test creating scope with multiple tools in allowlist."""
        scope = self.registry.create_scope(
            "test_agent",
            allowed_tools=["read_file", "write_file", "list_dir"]
        )
        assert "read_file" in scope._allowed_tools
        assert "write_file" in scope._allowed_tools
        assert "list_dir" in scope._allowed_tools

    def test_scope_allowlist_is_set(self):
        """Test allowlist is stored as a set for O(1) lookup."""
        scope = self.registry.create_scope("test_agent", allowed_tools=["tool1", "tool2"])
        assert isinstance(scope._allowed_tools, set)


# =============================================================================
# AgentScope Allowlist Filtering Tests
# =============================================================================

class TestAgentScopeAllowlist:
    """Tests for AgentScope allowlist filtering."""

    def setup_method(self):
        """Reset registry and clear tools before each test."""
        ToolRegistry._instance = None
        self.registry = ToolRegistry.get_instance()
        # Clear any tools from previous tests
        with self.registry._registry_lock:
            self.registry._tools.clear()

    def teardown_method(self):
        """Reset singleton after each test."""
        ToolRegistry._instance = None

    def test_scope_with_no_restrictions_sees_all_tools(self):
        """Test scope with None allowlist sees all registered tools."""
        self.registry.register("tool1", lambda: None)
        self.registry.register("tool2", lambda: None)
        self.registry.register("tool3", lambda: None)

        scope = self.registry.create_scope("test_agent", allowed_tools=None)
        available = scope.get_available_tools()

        assert "tool1" in available
        assert "tool2" in available
        assert "tool3" in available
        assert len(available) == 3

    def test_scope_with_allowlist_sees_subset(self):
        """Test scope with allowlist sees only allowed tools."""
        self.registry.register("tool1", lambda: None)
        self.registry.register("tool2", lambda: None)
        self.registry.register("tool3", lambda: None)

        scope = self.registry.create_scope(
            "test_agent",
            allowed_tools=["tool1", "tool2"]
        )
        available = scope.get_available_tools()

        assert "tool1" in available
        assert "tool2" in available
        assert "tool3" not in available
        assert len(available) == 2

    def test_scope_with_empty_allowlist_sees_no_tools(self):
        """Test scope with empty allowlist sees no tools.

        An empty allowlist [] means NO tools are accessible.
        This is different from None which means all tools are accessible.
        """
        # Create scope with empty allowlist - no tools should be accessible
        scope = self.registry.create_scope("test_agent", allowed_tools=[])

        # Register some tools
        self.registry.register("tool1", lambda: None)
        self.registry.register("tool2", lambda: None)

        # With empty allowlist, NO tools are visible
        available = scope.get_available_tools()
        assert "tool1" not in available
        assert "tool2" not in available
        assert len(available) == 0

    def test_scope_filters_nonexistent_tools(self):
        """Test scope allowlist filters out non-existent tools."""
        self.registry.register("existing_tool", lambda: None)

        scope = self.registry.create_scope(
            "test_agent",
            allowed_tools=["existing_tool", "nonexistent_tool"]
        )
        available = scope.get_available_tools()

        # Only existing tool should be in available tools
        assert "existing_tool" in available
        assert "nonexistent_tool" not in available


# =============================================================================
# AgentScope Tool Execution Tests
# =============================================================================

class TestAgentScopeExecution:
    """Tests for AgentScope tool execution."""

    def setup_method(self):
        """Reset registry and clear tools before each test."""
        ToolRegistry._instance = None
        self.registry = ToolRegistry.get_instance()
        # Clear any tools from previous tests
        with self.registry._registry_lock:
            self.registry._tools.clear()

    def teardown_method(self):
        """Reset singleton after each test."""
        ToolRegistry._instance = None

    def test_execute_allowed_tool(self):
        """Test executing tool within allowlist succeeds."""
        def add(a, b):
            return a + b

        self.registry.register("add", add)
        scope = self.registry.create_scope("test_agent", allowed_tools=["add"])
        result = scope.execute_tool("add", 2, 3)
        assert result == 5

    def test_execute_allowed_tool_with_kwargs(self):
        """Test executing allowed tool with keyword arguments."""
        def greet(name, greeting="Hello"):
            return f"{greeting}, {name}!"

        self.registry.register("greet", greet)
        scope = self.registry.create_scope("test_agent", allowed_tools=["greet"])
        result = scope.execute_tool("greet", name="World", greeting="Hi")
        assert result == "Hi, World!"

    def test_execute_denied_tool_raises_access_denied(self):
        """Test executing tool outside allowlist raises ToolAccessDeniedError."""
        self.registry.register("secret_tool", lambda: "secret")
        scope = self.registry.create_scope(
            "test_agent",
            allowed_tools=["other_tool"]
        )

        with pytest.raises(ToolAccessDeniedError) as exc_info:
            scope.execute_tool("secret_tool")

        assert exc_info.value.tool_name == "secret_tool"
        assert exc_info.value.agent_id == "test_agent"

    def test_execute_nonexistent_tool_in_allowlist_raises_not_found(self):
        """Test executing non-existent tool (even if in allowlist) raises ToolNotFoundError."""
        # Register tool in allowlist but not in registry
        scope = self.registry.create_scope(
            "test_agent",
            allowed_tools=["nonexistent_tool"]
        )

        with pytest.raises(ToolNotFoundError):
            scope.execute_tool("nonexistent_tool")

    def test_execute_denied_tool_error_has_agent_id(self):
        """Test ToolAccessDeniedError contains agent identifier."""
        self.registry.register("restricted_tool", lambda: None)
        scope = self.registry.create_scope(
            "specific_agent",
            allowed_tools=["other_tool"]
        )

        with pytest.raises(ToolAccessDeniedError) as exc_info:
            scope.execute_tool("restricted_tool")

        assert exc_info.value.agent_id == "specific_agent"


# =============================================================================
# AgentScope has_tool Tests
# =============================================================================

class TestAgentScopeHasTool:
    """Tests for AgentScope.has_tool() method."""

    def setup_method(self):
        """Reset registry and clear tools before each test."""
        ToolRegistry._instance = None
        self.registry = ToolRegistry.get_instance()
        # Clear any tools from previous tests
        with self.registry._registry_lock:
            self.registry._tools.clear()

    def teardown_method(self):
        """Reset singleton after each test."""
        ToolRegistry._instance = None

    def test_has_tool_allowed_and_exists(self):
        """Test has_tool returns True for allowed tool that exists."""
        self.registry.register("my_tool", lambda: None)
        scope = self.registry.create_scope(
            "test_agent",
            allowed_tools=["my_tool"]
        )
        assert scope.has_tool("my_tool") is True

    def test_has_tool_allowed_but_not_exists(self):
        """Test has_tool returns False for allowed tool that doesn't exist."""
        scope = self.registry.create_scope(
            "test_agent",
            allowed_tools=["nonexistent_tool"]
        )
        assert scope.has_tool("nonexistent_tool") is False

    def test_has_tool_exists_but_not_allowed(self):
        """Test has_tool returns False for existing tool not in allowlist."""
        self.registry.register("restricted_tool", lambda: None)
        scope = self.registry.create_scope(
            "test_agent",
            allowed_tools=["other_tool"]
        )
        assert scope.has_tool("restricted_tool") is False

    def test_has_tool_neither_exists_nor_allowed(self):
        """Test has_tool returns False for tool that neither exists nor is allowed."""
        scope = self.registry.create_scope(
            "test_agent",
            allowed_tools=[]
        )
        assert scope.has_tool("random_tool") is False

    def test_has_tool_with_none_allowlist(self):
        """Test has_tool with None allowlist checks only existence."""
        self.registry.register("any_tool", lambda: None)
        scope = self.registry.create_scope("test_agent", allowed_tools=None)

        assert scope.has_tool("any_tool") is True
        assert scope.has_tool("nonexistent_tool") is False


# =============================================================================
# SEC-001: Case-Sensitive Matching Tests
# =============================================================================

class TestCaseSensitiveMatching:
    """
    Security tests for case-sensitive tool name matching (SEC-001).

    These tests verify that tool names must match exactly, preventing
    allowlist bypass attempts via case variation attacks.
    """

    def setup_method(self):
        """Reset registry and clear tools before each test."""
        ToolRegistry._instance = None
        self.registry = ToolRegistry.get_instance()
        # Clear any tools from previous tests
        with self.registry._registry_lock:
            self.registry._tools.clear()

    def teardown_method(self):
        """Reset singleton after each test."""
        ToolRegistry._instance = None

    def test_case_sensitive_exact_match(self):
        """Test exact case match is required for tool access."""
        self.registry.register("file_read", lambda: "read")
        scope = self.registry.create_scope(
            "test_agent",
            allowed_tools=["file_read"]
        )

        # Exact match works
        assert scope.has_tool("file_read") is True
        result = scope.execute_tool("file_read")
        assert result == "read"

    def test_case_sensitive_uppercase_variation_denied(self):
        """Test uppercase variation is denied (SEC-001)."""
        self.registry.register("file_read", lambda: "read")
        scope = self.registry.create_scope(
            "test_agent",
            allowed_tools=["file_read"]
        )

        assert scope.has_tool("FILE_READ") is False

        with pytest.raises(ToolAccessDeniedError):
            scope.execute_tool("FILE_READ")

    def test_case_sensitive_lowercase_variation_denied(self):
        """Test lowercase variation is denied when original has uppercase (SEC-001)."""
        self.registry.register("File_Read", lambda: "read")
        scope = self.registry.create_scope(
            "test_agent",
            allowed_tools=["File_Read"]
        )

        assert scope.has_tool("file_read") is False

        with pytest.raises(ToolAccessDeniedError):
            scope.execute_tool("file_read")

    def test_case_sensitive_mixed_case_variation_denied(self):
        """Test mixed case variation is denied (SEC-001)."""
        self.registry.register("file_read", lambda: "read")
        scope = self.registry.create_scope(
            "test_agent",
            allowed_tools=["file_read"]
        )

        assert scope.has_tool("File_Read") is False
        assert scope.has_tool("file_Read") is False
        assert scope.has_tool("File_read") is False
        assert scope.has_tool("FILE_read") is False

        with pytest.raises(ToolAccessDeniedError):
            scope.execute_tool("File_Read")

    def test_case_sensitive_pascal_case_denied(self):
        """Test PascalCase variation is denied (SEC-001)."""
        self.registry.register("read_file", lambda: "read")
        scope = self.registry.create_scope(
            "test_agent",
            allowed_tools=["read_file"]
        )

        assert scope.has_tool("ReadFile") is False
        assert scope.has_tool("readFile") is False
        assert scope.has_tool("Read_file") is False

    def test_allowlist_bypass_attempt_via_case(self):
        """Test allowlist cannot be bypassed via case manipulation (SEC-001)."""
        self.registry.register("secret_tool", lambda: "SECRET!")
        scope = self.registry.create_scope(
            "test_agent",
            allowed_tools=["public_tool"]
        )

        # Try various case manipulations
        bypass_attempts = [
            "Secret_tool",
            "secret_Tool",
            "SECRET_TOOL",
            "Secret_Tool",
            "seCret_toOl",
        ]

        for attempt in bypass_attempts:
            assert scope.has_tool(attempt) is False

            with pytest.raises(ToolAccessDeniedError):
                scope.execute_tool(attempt)

    def test_similar_tool_names_distinguished_by_case(self):
        """Test similar tool names are distinguished by case."""
        self.registry.register("read_file", lambda: "lowercase")
        self.registry.register("Read_File", lambda: "PascalCase")
        self.registry.register("READ_FILE", lambda: "UPPERCASE")

        # Scope with lowercase access
        scope_lower = self.registry.create_scope(
            "agent_lower",
            allowed_tools=["read_file"]
        )
        assert scope_lower.has_tool("read_file") is True
        assert scope_lower.has_tool("Read_File") is False
        assert scope_lower.has_tool("READ_FILE") is False

        # Scope with PascalCase access
        scope_pascal = self.registry.create_scope(
            "agent_pascal",
            allowed_tools=["Read_File"]
        )
        assert scope_pascal.has_tool("read_file") is False
        assert scope_pascal.has_tool("Read_File") is True
        assert scope_pascal.has_tool("READ_FILE") is False


# =============================================================================
# Multi-Agent Isolation Tests
# =============================================================================

class TestMultiAgentIsolation:
    """Tests for multi-agent tool isolation."""

    def setup_method(self):
        """Reset registry and clear tools before each test."""
        ToolRegistry._instance = None
        self.registry = ToolRegistry.get_instance()
        # Clear any tools from previous tests
        with self.registry._registry_lock:
            self.registry._tools.clear()

    def teardown_method(self):
        """Reset singleton after each test."""
        ToolRegistry._instance = None

    def test_multiple_agents_isolated(self):
        """Test multiple agents have isolated tool access."""
        self.registry.register("agent1_tool", lambda: "agent1")
        self.registry.register("agent2_tool", lambda: "agent2")
        self.registry.register("shared_tool", lambda: "shared")

        scope1 = self.registry.create_scope(
            "agent1",
            allowed_tools=["agent1_tool", "shared_tool"]
        )
        scope2 = self.registry.create_scope(
            "agent2",
            allowed_tools=["agent2_tool", "shared_tool"]
        )

        # Agent 1 can access its tools
        assert scope1.has_tool("agent1_tool") is True
        assert scope1.has_tool("shared_tool") is True
        assert scope1.has_tool("agent2_tool") is False

        # Agent 2 can access its tools
        assert scope2.has_tool("agent2_tool") is True
        assert scope2.has_tool("shared_tool") is True
        assert scope2.has_tool("agent1_tool") is False

    def test_agent_cannot_access_other_agent_exclusive_tool(self):
        """Test agent cannot access tool exclusive to another agent."""
        self.registry.register("exclusive_tool", lambda: "exclusive")
        self.registry.register("other_tool", lambda: "other")

        scope1 = self.registry.create_scope(
            "agent1",
            allowed_tools=["exclusive_tool"]
        )
        scope2 = self.registry.create_scope(
            "agent2",
            allowed_tools=["other_tool"]  # Different tool, not empty
        )

        assert scope1.has_tool("exclusive_tool") is True
        # scope2 cannot access exclusive_tool because it's not in its allowlist
        assert scope2.has_tool("exclusive_tool") is False

        # Agent 2 should be denied
        with pytest.raises(ToolAccessDeniedError):
            scope2.execute_tool("exclusive_tool")

    def test_agent_executions_isolated(self):
        """Test tool executions by different agents are isolated."""
        execution_log = []

        def logged_tool():
            execution_log.append("executed")
            return "executed"

        self.registry.register("logged_tool", logged_tool)

        scope1 = self.registry.create_scope("agent1", allowed_tools=["logged_tool"])
        scope2 = self.registry.create_scope("agent2", allowed_tools=["logged_tool"])

        result1 = scope1.execute_tool("logged_tool")
        result2 = scope2.execute_tool("logged_tool")

        assert result1 == "executed"
        assert result2 == "executed"
        assert len(execution_log) == 2


# =============================================================================
# Cleanup and Memory Management Tests
# =============================================================================

class TestAgentScopeCleanup:
    """Tests for AgentScope cleanup() behavior (MEM-001)."""

    def setup_method(self):
        """Reset registry and clear tools before each test."""
        ToolRegistry._instance = None
        self.registry = ToolRegistry.get_instance()
        # Clear any tools from previous tests
        with self.registry._registry_lock:
            self.registry._tools.clear()

    def teardown_method(self):
        """Reset singleton after each test."""
        ToolRegistry._instance = None

    def test_cleanup_releases_allowlist(self):
        """Test cleanup() releases allowlist reference."""
        scope = self.registry.create_scope(
            "test_agent",
            allowed_tools=["tool1", "tool2"]
        )

        assert scope._allowed_tools is not None
        scope.cleanup()
        assert scope._allowed_tools is None

    def test_cleanup_releases_registry_reference(self):
        """Test cleanup() releases registry reference."""
        scope = self.registry.create_scope("test_agent", allowed_tools=["tool1"])

        assert scope._registry is not None
        scope.cleanup()
        assert scope._registry is None

    def test_cleanup_multiple_times_safe(self):
        """Test calling cleanup() multiple times is safe (no exceptions)."""
        scope = self.registry.create_scope("test_agent", allowed_tools=["tool1"])

        # Should not raise any exceptions
        scope.cleanup()
        scope.cleanup()
        scope.cleanup()

        assert scope._registry is None
        assert scope._allowed_tools is None

    def test_cleanup_then_execute_raises_exception(self):
        """Test executing tool after cleanup raises appropriate exception."""
        self.registry.register("test_tool", lambda: "value")
        scope = self.registry.create_scope("test_agent", allowed_tools=["test_tool"])

        scope.cleanup()

        # After cleanup, _registry is None, so execute should fail
        # The behavior depends on implementation - may raise AttributeError
        # or ToolAccessDeniedError
        with pytest.raises(Exception):
            scope.execute_tool("test_tool")

    def test_cleanup_memory_leak_detection(self):
        """Test cleanup prevents memory leaks (MEM-001)."""
        import gc
        import weakref

        self.registry.register("test_tool", lambda: None)
        scope = self.registry.create_scope("test_agent", allowed_tools=["test_tool"])

        # Create weak reference to scope
        scope_ref = weakref.ref(scope)

        # Cleanup and delete reference
        scope.cleanup()
        del scope
        gc.collect()

        # Scope should be garbage collected
        assert scope_ref() is None, "Memory leak: scope not garbage collected after cleanup"


# =============================================================================
# Thread Safety Tests
# =============================================================================

class TestAgentScopeThreadSafety:
    """Thread safety tests for AgentScope operations."""

    def setup_method(self):
        """Reset registry and clear tools before each test."""
        ToolRegistry._instance = None
        self.registry = ToolRegistry.get_instance()
        # Clear any tools from previous tests
        with self.registry._registry_lock:
            self.registry._tools.clear()

    def teardown_method(self):
        """Reset singleton after each test."""
        ToolRegistry._instance = None

    def test_concurrent_tool_execution(self):
        """Test concurrent tool execution from multiple scopes."""
        self.registry.register("counter_tool", lambda: 1)

        results = []
        errors = []
        lock = threading.Lock()

        def execute_tool(agent_id):
            try:
                scope = self.registry.create_scope(agent_id, allowed_tools=["counter_tool"])
                for _ in range(10):
                    result = scope.execute_tool("counter_tool")
                    with lock:
                        results.append((agent_id, result))
            except Exception as e:
                with lock:
                    errors.append((agent_id, e))

        # 50 agents, 10 executions each = 500 total executions
        with ThreadPoolExecutor(max_workers=50) as executor:
            futures = [
                executor.submit(execute_tool, f"agent_{i}")
                for i in range(50)
            ]
            for future in as_completed(futures):
                future.result()

        assert len(errors) == 0, f"Thread safety errors: {errors}"
        assert len(results) == 500

    def test_concurrent_scope_creation(self):
        """Test concurrent scope creation is thread-safe."""
        scopes = []
        errors = []
        lock = threading.Lock()

        def create_scope(agent_id):
            try:
                scope = self.registry.create_scope(agent_id, allowed_tools=None)
                with lock:
                    scopes.append(scope)
            except Exception as e:
                with lock:
                    errors.append((agent_id, e))

        with ThreadPoolExecutor(max_workers=100) as executor:
            futures = [
                executor.submit(create_scope, f"agent_{i}")
                for i in range(100)
            ]
            for future in as_completed(futures):
                future.result()

        assert len(errors) == 0, f"Thread safety errors: {errors}"
        assert len(scopes) == 100

        # All scopes should have unique agent IDs
        agent_ids = [s.get_agent_id() for s in scopes]
        assert len(set(agent_ids)) == 100

    def test_concurrent_allowlist_check(self):
        """Test concurrent has_tool checks are thread-safe."""
        self.registry.register("shared_tool", lambda: None)

        results = []
        lock = threading.Lock()

        def check_tool(agent_id):
            scope = self.registry.create_scope(agent_id, allowed_tools=["shared_tool"])
            for _ in range(10):
                result = scope.has_tool("shared_tool")
                with lock:
                    results.append(result)

        with ThreadPoolExecutor(max_workers=50) as executor:
            futures = [
                executor.submit(check_tool, f"agent_{i}")
                for i in range(50)
            ]
            for future in as_completed(futures):
                future.result()

        # All checks should return True
        assert all(results), "Some has_tool checks returned False unexpectedly"
        assert len(results) == 500  # 50 agents * 10 checks


# =============================================================================
# Edge Case Tests
# =============================================================================

class TestAgentScopeEdgeCases:
    """Edge case tests for AgentScope."""

    def setup_method(self):
        """Reset registry and clear tools before each test."""
        ToolRegistry._instance = None
        self.registry = ToolRegistry.get_instance()
        # Clear any tools from previous tests
        with self.registry._registry_lock:
            self.registry._tools.clear()

    def teardown_method(self):
        """Reset singleton after each test."""
        ToolRegistry._instance = None

    def test_tool_name_with_special_characters(self):
        """Test tool names with special characters are handled correctly."""
        self.registry.register("tool-with-dash", lambda: "dash")
        self.registry.register("tool_with_underscore", lambda: "underscore")
        self.registry.register("tool.with.dot", lambda: "dot")

        scope = self.registry.create_scope(
            "test_agent",
            allowed_tools=["tool-with-dash", "tool_with_underscore", "tool.with.dot"]
        )

        assert scope.has_tool("tool-with-dash") is True
        assert scope.has_tool("tool_with_underscore") is True
        assert scope.has_tool("tool.with.dot") is True

    def test_tool_name_with_numbers(self):
        """Test tool names with numbers are handled correctly."""
        self.registry.register("tool123", lambda: "numbered")
        self.registry.register("v2_tool", lambda: "versioned")

        scope = self.registry.create_scope(
            "test_agent",
            allowed_tools=["tool123", "v2_tool"]
        )

        assert scope.has_tool("tool123") is True
        assert scope.has_tool("v2_tool") is True

    def test_unicode_tool_names(self):
        """Test unicode tool names are handled correctly."""
        self.registry.register("tool_\u03b1", lambda: "alpha")  # Greek alpha
        self.registry.register("\u4e2d\u6587_tool", lambda: "chinese")

        scope = self.registry.create_scope(
            "test_agent",
            allowed_tools=["tool_\u03b1", "\u4e2d\u6587_tool"]
        )

        assert scope.has_tool("tool_\u03b1") is True
        assert scope.has_tool("\u4e2d\u6587_tool") is True

    def test_very_long_tool_name(self):
        """Test very long tool names are handled correctly."""
        long_name = "a" * 1000
        self.registry.register(long_name, lambda: "long")

        scope = self.registry.create_scope(
            "test_agent",
            allowed_tools=[long_name]
        )

        assert scope.has_tool(long_name) is True
