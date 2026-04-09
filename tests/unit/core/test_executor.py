# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Unit tests for AgentExecutor.

This test suite validates:
- AgentExecutor creation and configuration
- Behavior injection and execution
- Lifecycle hooks (before, after, error)
- Error handling and recovery strategies
- Async execution support
- Thread safety

Quality Gate 4 Criteria Covered:
- MOD-002: AgentExecutor behavior injection (zero regression)
- THREAD-004: Thread safety (100+ concurrent threads)
"""

import asyncio
import pytest
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from gaia.core.capabilities import AgentCapabilities
from gaia.core.executor import (
    AgentExecutor,
    ExecutionContext,
    ExecutionResult,
)
from gaia.core.profile import AgentProfile


# =============================================================================
# ExecutionContext Tests
# =============================================================================

class TestExecutionContext:
    """Tests for ExecutionContext dataclass."""

    def test_create_default_context(self):
        """Test creating context with default values."""
        ctx = ExecutionContext(prompt="Test prompt")
        assert ctx.prompt == "Test prompt"
        assert ctx.context == {}
        assert ctx.execution_id != ""
        assert ctx.metadata == {}

    def test_create_context_with_all_fields(self):
        """Test creating context with all fields."""
        ctx = ExecutionContext(
            prompt="Test",
            context={"key": "value"},
            execution_id="custom-id",
            metadata={"meta": "data"},
        )
        assert ctx.prompt == "Test"
        assert ctx.context == {"key": "value"}
        assert ctx.execution_id == "custom-id"
        assert ctx.metadata == {"meta": "data"}

    def test_auto_generates_execution_id(self):
        """Test that execution_id is auto-generated."""
        ctx1 = ExecutionContext(prompt="Test 1")
        ctx2 = ExecutionContext(prompt="Test 2")
        assert ctx1.execution_id != ""
        assert ctx2.execution_id != ""
        assert ctx1.execution_id != ctx2.execution_id

    def test_context_mutable_fields_are_copies(self):
        """Test that context and metadata are copies."""
        orig_ctx = {"key": "value"}
        orig_meta = {"meta": "data"}
        ctx = ExecutionContext(
            prompt="Test",
            context=orig_ctx,
            metadata=orig_meta,
        )
        orig_ctx["key2"] = "value2"
        orig_meta["meta2"] = "data2"
        assert ctx.context == {"key": "value"}
        assert ctx.metadata == {"meta": "data"}


# =============================================================================
# ExecutionResult Tests
# =============================================================================

class TestExecutionResult:
    """Tests for ExecutionResult dataclass."""

    def test_create_default_result(self):
        """Test creating result with default values."""
        result = ExecutionResult()
        assert result.success is False
        assert result.output is None
        assert result.error is None
        assert result.execution_id == ""
        assert result.metadata == {}

    def test_create_result_with_all_fields(self):
        """Test creating result with all fields."""
        result = ExecutionResult(
            success=True,
            output="Test output",
            execution_id="test-id",
            metadata={"key": "value"},
        )
        assert result.success is True
        assert result.output == "Test output"
        assert result.execution_id == "test-id"

    def test_to_dict(self):
        """Test converting to dictionary."""
        result = ExecutionResult(
            success=True,
            output="output",
            error=None,
            execution_id="id",
        )
        d = result.to_dict()
        assert d["success"] is True
        assert d["output"] == "output"
        assert d["error"] is None


# =============================================================================
# AgentExecutor Creation Tests
# =============================================================================

class TestAgentExecutorCreation:
    """Tests for AgentExecutor creation and initialization."""

    def test_create_default_executor(self):
        """Test creating executor with default values."""
        executor = AgentExecutor()
        assert executor.profile is not None
        assert executor.profile.name == "Unnamed Agent"
        assert executor._behavior is None
        assert executor._before_hook is None
        assert executor._after_hook is None
        assert executor._error_handler is None
        assert executor._error_recovery_strategy == "raise"
        assert executor._max_retries == 3

    def test_create_executor_with_profile(self):
        """Test creating executor with custom profile."""
        profile = AgentProfile(name="Custom Agent")
        executor = AgentExecutor(profile=profile)
        assert executor.profile.name == "Custom Agent"

    def test_create_executor_with_default_behavior(self):
        """Test creating executor with default behavior."""
        def default_behavior(ctx):
            return "default"

        executor = AgentExecutor(default_behavior=default_behavior)
        assert executor._behavior == default_behavior


# =============================================================================
# AgentExecutor Behavior Injection Tests
# =============================================================================

class TestAgentExecutorBehaviorInjection:
    """Tests for AgentExecutor behavior injection."""

    def test_inject_behavior(self):
        """Test injecting behavior."""
        executor = AgentExecutor()
        behavior = lambda ctx: f"Processed: {ctx.prompt}"
        executor.inject_behavior(behavior)
        assert executor._behavior == behavior

    def test_execute_with_behavior(self):
        """Test executing with injected behavior."""
        executor = AgentExecutor()
        executor.inject_behavior(lambda ctx: f"Result: {ctx.prompt}")
        result = executor.execute("Hello")
        assert result.success is True
        assert result.output == "Result: Hello"

    def test_execute_without_behavior_raises(self):
        """Test executing without behavior raises ValueError."""
        executor = AgentExecutor()
        result = executor.execute("Hello")
        assert result.success is False
        assert "No behavior injected" in result.error

    def test_execute_passes_context(self):
        """Test that context is passed to behavior."""
        received_context = {}

        def behavior(ctx):
            received_context.update(ctx.context)
            return "done"

        executor = AgentExecutor(default_behavior=behavior)
        executor.execute("Test", context={"key": "value"})
        assert received_context == {"key": "value"}

    def test_execute_with_metadata(self):
        """Test executing with metadata."""
        def behavior(ctx):
            return ctx.metadata.get("custom", "default")

        executor = AgentExecutor(default_behavior=behavior)
        result = executor.execute("Test", metadata={"custom": "value"})
        assert result.output == "value"


# =============================================================================
# AgentExecutor Hook Tests
# =============================================================================

class TestAgentExecutorHooks:
    """Tests for AgentExecutor lifecycle hooks."""

    def test_set_before_hook(self):
        """Test setting before hook."""
        executor = AgentExecutor()
        hook_called = False

        def before_hook(ctx):
            nonlocal hook_called
            hook_called = True

        executor.set_before_hook(before_hook)
        executor.inject_behavior(lambda ctx: "result")
        executor.execute("Test")
        assert hook_called is True

    def test_before_hook_can_modify_context(self):
        """Test that before hook can modify context."""
        def before_hook(ctx):
            ctx.context["modified"] = True

        def behavior(ctx):
            return ctx.context.get("modified", False)

        executor = AgentExecutor(default_behavior=behavior)
        executor.set_before_hook(before_hook)
        result = executor.execute("Test")
        assert result.output is True

    def test_set_after_hook(self):
        """Test setting after hook."""
        executor = AgentExecutor()
        hook_result = None

        def after_hook(ctx, result):
            nonlocal hook_result
            hook_result = result.output

        executor.set_after_hook(after_hook)
        executor.inject_behavior(lambda ctx: "test_output")
        executor.execute("Test")
        assert hook_result == "test_output"

    def test_after_hook_can_modify_result(self):
        """Test that after hook can modify result."""
        def after_hook(ctx, result):
            result.output = f"Modified: {result.output}"

        executor = AgentExecutor(default_behavior=lambda ctx: "original")
        executor.set_after_hook(after_hook)
        result = executor.execute("Test")
        assert result.output == "Modified: original"

    def test_set_error_handler(self):
        """Test setting error handler."""
        executor = AgentExecutor()
        error_received = None

        def error_handler(ctx, error):
            nonlocal error_received
            error_received = str(error)

        def failing_behavior(ctx):
            raise ValueError("Test error")

        executor.set_error_handler(error_handler)
        executor.inject_behavior(failing_behavior)
        result = executor.execute("Test")
        assert "Test error" in error_received
        assert result.success is False

    def test_all_hooks_together(self):
        """Test using all hooks together."""
        execution_log = []

        def before_hook(ctx):
            execution_log.append("before")

        def behavior(ctx):
            execution_log.append("behavior")
            return "result"

        def after_hook(ctx, result):
            execution_log.append("after")

        executor = AgentExecutor(default_behavior=behavior)
        executor.set_before_hook(before_hook)
        executor.set_after_hook(after_hook)
        executor.execute("Test")

        assert execution_log == ["before", "behavior", "after"]


# =============================================================================
# AgentExecutor Error Handling Tests
# =============================================================================

class TestAgentExecutorErrorHandling:
    """Tests for AgentExecutor error handling."""

    def test_error_recovery_raise(self):
        """Test raise recovery strategy."""
        executor = AgentExecutor()
        executor.set_error_handler(
            lambda ctx, e: None,
            recovery_strategy="raise",
            max_retries=0,
        )
        executor.inject_behavior(lambda ctx: (_ for _ in ()).throw(ValueError("test")))
        result = executor.execute("Test")
        assert result.success is False
        assert "test" in result.error

    def test_error_recovery_return_default(self):
        """Test return_default recovery strategy."""
        executor = AgentExecutor()
        executor.set_error_handler(
            lambda ctx, e: None,
            recovery_strategy="return_default",
        )
        executor.inject_behavior(lambda ctx: (_ for _ in ()).throw(ValueError("test")))
        result = executor.execute("Test")
        assert result.success is False
        assert "test" in result.error

    def test_error_recovery_retry(self):
        """Test retry recovery strategy."""
        attempt_count = [0]

        def failing_then_succeeding_behavior(ctx):
            attempt_count[0] += 1
            if attempt_count[0] < 3:
                raise ValueError(f"Attempt {attempt_count[0]} failed")
            return f"Succeeded on attempt {attempt_count[0]}"

        executor = AgentExecutor()
        executor.set_error_handler(
            lambda ctx, e: None,
            recovery_strategy="retry",
            max_retries=3,
            retry_delay=0.01,
        )
        executor.inject_behavior(failing_then_succeeding_behavior)
        result = executor.execute("Test")
        assert result.success is True
        assert attempt_count[0] == 3

    def test_error_recovery_retry_exhausted(self):
        """Test retry recovery with exhausted retries."""
        attempt_count = [0]

        def always_failing_behavior(ctx):
            attempt_count[0] += 1
            raise ValueError("Always fails")

        executor = AgentExecutor()
        executor.set_error_handler(
            lambda ctx, e: None,
            recovery_strategy="retry",
            max_retries=2,
            retry_delay=0.01,
        )
        executor.inject_behavior(always_failing_behavior)
        result = executor.execute("Test")
        assert result.success is False
        assert attempt_count[0] == 3  # Initial + 2 retries


# =============================================================================
# AgentExecutor Async Tests
# =============================================================================

class TestAgentExecutorAsync:
    """Tests for AgentExecutor async execution."""

    @pytest.mark.asyncio
    async def test_execute_async_with_sync_behavior(self):
        """Test async execution with sync behavior."""
        executor = AgentExecutor()
        executor.inject_behavior(lambda ctx: f"Result: {ctx.prompt}")
        result = await executor.execute_async("Hello")
        assert result.success is True
        assert result.output == "Result: Hello"

    @pytest.mark.asyncio
    async def test_execute_async_with_async_behavior(self):
        """Test async execution with async behavior."""
        async def async_behavior(ctx):
            await asyncio.sleep(0.01)
            return f"Async: {ctx.prompt}"

        executor = AgentExecutor()
        executor.inject_behavior(async_behavior)
        result = await executor.execute_async("Hello")
        assert result.success is True
        assert result.output == "Async: Hello"

    @pytest.mark.asyncio
    async def test_execute_async_with_async_hooks(self):
        """Test async execution with async hooks."""
        hook_executed = False

        async def async_before_hook(ctx):
            nonlocal hook_executed
            await asyncio.sleep(0.001)
            hook_executed = True

        executor = AgentExecutor()
        executor.set_before_hook(async_before_hook)
        executor.inject_behavior(lambda ctx: "result")
        result = await executor.execute_async("Test")
        assert hook_executed is True
        assert result.success is True

    @pytest.mark.asyncio
    async def test_execute_async_error_recovery(self):
        """Test async error recovery."""
        attempt_count = [0]

        async def failing_then_succeeding(ctx):
            attempt_count[0] += 1
            if attempt_count[0] < 2:
                raise ValueError("Fail")
            return "Success"

        executor = AgentExecutor()
        executor.set_error_handler(
            lambda ctx, e: None,
            recovery_strategy="retry",
            max_retries=2,
            retry_delay=0.01,
        )
        executor.inject_behavior(failing_then_succeeding)
        result = await executor.execute_async("Test")
        assert result.success is True
        assert attempt_count[0] == 2


# =============================================================================
# AgentExecutor Execution History Tests
# =============================================================================

class TestAgentExecutorExecutionHistory:
    """Tests for AgentExecutor execution history."""

    def test_get_execution_history(self):
        """Test getting execution history."""
        executor = AgentExecutor()
        executor.inject_behavior(lambda ctx: "result")
        executor.execute("Test 1")
        executor.execute("Test 2")
        history = executor.get_execution_history()
        assert len(history) == 2

    def test_execution_history_limits(self):
        """Test execution history limit."""
        executor = AgentExecutor()
        executor._max_history = 5
        executor.inject_behavior(lambda ctx: "result")
        for i in range(10):
            executor.execute(f"Test {i}")
        history = executor.get_execution_history()
        assert len(history) == 5

    def test_clear_execution_history(self):
        """Test clearing execution history."""
        executor = AgentExecutor()
        executor.inject_behavior(lambda ctx: "result")
        executor.execute("Test 1")
        executor.execute("Test 2")
        executor.clear_execution_history()
        history = executor.get_execution_history()
        assert len(history) == 0

    def test_execution_history_records_success(self):
        """Test that history records success status."""
        executor = AgentExecutor()
        executor.inject_behavior(lambda ctx: "success")
        executor.execute("Test")
        history = executor.get_execution_history()
        assert len(history) == 1
        assert history[0]["success"] is True

    def test_execution_history_records_failure(self):
        """Test that history records failure status."""
        executor = AgentExecutor()
        executor.inject_behavior(lambda ctx: (_ for _ in ()).throw(ValueError("fail")))
        executor.execute("Test")
        history = executor.get_execution_history()
        assert len(history) == 1
        assert history[0]["success"] is False


# =============================================================================
# AgentExecutor Status and Capabilities Tests
# =============================================================================

class TestAgentExecutorStatus:
    """Tests for AgentExecutor status and capabilities."""

    def test_get_capabilities(self):
        """Test getting capabilities from executor."""
        caps = AgentCapabilities(supports_vision=True)
        profile = AgentProfile(name="Test", capabilities=caps)
        executor = AgentExecutor(profile=profile)
        retrieved_caps = executor.get_capabilities()
        assert retrieved_caps.supports_vision is True

    def test_get_capabilities_returns_copy(self):
        """Test that get_capabilities returns a copy."""
        caps = AgentCapabilities()
        profile = AgentProfile(name="Test", capabilities=caps)
        executor = AgentExecutor(profile=profile)
        retrieved_caps = executor.get_capabilities()
        retrieved_caps.add_tool("new_tool")
        assert "new_tool" not in executor.profile.capabilities.supported_tools

    def test_get_status(self):
        """Test getting executor status."""
        executor = AgentExecutor()
        status = executor.get_status()
        assert "profile_name" in status
        assert "has_behavior" in status
        assert "execution_count" in status

    def test_get_status_with_hooks(self):
        """Test status with hooks set."""
        executor = AgentExecutor()
        executor.set_before_hook(lambda ctx: None)
        executor.set_after_hook(lambda ctx, r: None)
        executor.set_error_handler(lambda ctx, e: None)
        executor.inject_behavior(lambda ctx: "result")
        status = executor.get_status()
        assert status["has_before_hook"] is True
        assert status["has_after_hook"] is True
        assert status["has_error_handler"] is True
        assert status["has_behavior"] is True

    def test_repr(self):
        """Test string representation."""
        executor = AgentExecutor()
        executor.inject_behavior(lambda ctx: "result")
        executor.execute("Test")
        repr_str = repr(executor)
        assert "AgentExecutor" in repr_str
        assert "behavior=set" in repr_str
        assert "executions=1" in repr_str


# =============================================================================
# AgentExecutor Thread Safety Tests
# =============================================================================

class TestAgentExecutorThreadSafety:
    """Thread safety tests for AgentExecutor."""

    def test_concurrent_execution(self):
        """Test concurrent execute calls."""
        executor = AgentExecutor()
        executor.inject_behavior(lambda ctx: f"Result: {ctx.prompt}")
        results = []
        errors = []
        lock = threading.Lock()

        def execute(thread_id):
            try:
                for i in range(10):
                    result = executor.execute(f"Thread {thread_id} - {i}")
                    with lock:
                        results.append((thread_id, result.success))
            except Exception as e:
                with lock:
                    errors.append((thread_id, e))

        with ThreadPoolExecutor(max_workers=20) as executor_pool:
            futures = [executor_pool.submit(execute, i) for i in range(20)]
            for future in as_completed(futures):
                future.result()

        assert len(errors) == 0
        assert len(results) == 200
        assert all(r[1] is True for r in results)

    def test_concurrent_behavior_injection(self):
        """Test concurrent behavior injection."""
        executor = AgentExecutor()
        errors = []
        lock = threading.Lock()

        def inject_behavior(behavior_id):
            try:
                executor.inject_behavior(lambda ctx, bid=behavior_id: f"Behavior {bid}")
            except Exception as e:
                with lock:
                    errors.append(e)

        with ThreadPoolExecutor(max_workers=50) as executor_pool:
            futures = [executor_pool.submit(inject_behavior, i) for i in range(50)]
            for future in as_completed(futures):
                future.result()

        assert len(errors) == 0

    def test_concurrent_hook_setting(self):
        """Test concurrent hook setting."""
        executor = AgentExecutor()
        errors = []
        lock = threading.Lock()

        def set_hooks(hook_id):
            try:
                executor.set_before_hook(lambda ctx, hid=hook_id: None)
                executor.set_after_hook(lambda ctx, r, hid=hook_id: None)
            except Exception as e:
                with lock:
                    errors.append(e)

        with ThreadPoolExecutor(max_workers=30) as executor_pool:
            futures = [executor_pool.submit(set_hooks, i) for i in range(30)]
            for future in as_completed(futures):
                future.result()

        assert len(errors) == 0

    def test_concurrent_history_access(self):
        """Test concurrent history access."""
        executor = AgentExecutor()
        executor._max_history = 100
        executor.inject_behavior(lambda ctx: "result")
        errors = []
        lock = threading.Lock()

        def access_history(thread_id):
            try:
                for _ in range(10):
                    executor.execute(f"Test {thread_id}")
                    history = executor.get_execution_history()
                    with lock:
                        lock.acquire()
                        lock.release()
            except Exception as e:
                with lock:
                    errors.append(e)

        with ThreadPoolExecutor(max_workers=10) as executor_pool:
            futures = [executor_pool.submit(access_history, i) for i in range(10)]
            for future in as_completed(futures):
                future.result()

        assert len(errors) == 0

    def test_100_concurrent_threads(self):
        """Test 100+ concurrent threads (THREAD-004 requirement)."""
        executor = AgentExecutor()
        executor.inject_behavior(lambda ctx: "result")
        results = []
        errors = []
        lock = threading.Lock()

        def execute(thread_id):
            try:
                result = executor.execute(f"Thread {thread_id}")
                with lock:
                    results.append(result.success)
            except Exception as e:
                with lock:
                    errors.append(e)

        with ThreadPoolExecutor(max_workers=100) as executor_pool:
            futures = [executor_pool.submit(execute, i) for i in range(100)]
            for future in as_completed(futures):
                future.result()

        assert len(errors) == 0
        assert len(results) == 100
        assert all(results)


# =============================================================================
# Edge Case Tests
# =============================================================================

class TestAgentExecutorEdgeCases:
    """Edge case tests for AgentExecutor."""

    def test_execute_with_none_context(self):
        """Test executing with None context."""
        executor = AgentExecutor()
        executor.inject_behavior(lambda ctx: ctx.context)
        result = executor.execute("Test", context=None)
        assert result.output == {}

    def test_execute_with_empty_prompt(self):
        """Test executing with empty prompt."""
        executor = AgentExecutor()
        executor.inject_behavior(lambda ctx: ctx.prompt)
        result = executor.execute("")
        assert result.output == ""

    def test_behavior_returning_none(self):
        """Test behavior that returns None."""
        executor = AgentExecutor()
        executor.inject_behavior(lambda ctx: None)
        result = executor.execute("Test")
        assert result.success is True
        assert result.output is None

    def test_behavior_raising_exception(self):
        """Test behavior that raises exception."""
        executor = AgentExecutor()
        executor.inject_behavior(lambda ctx: (_ for _ in ()).throw(RuntimeError("test")))
        result = executor.execute("Test")
        assert result.success is False
        assert "test" in result.error

    def test_hook_raising_exception(self):
        """Test hook that raises exception."""
        def failing_before_hook(ctx):
            raise ValueError("Hook error")

        executor = AgentExecutor()
        executor.set_before_hook(failing_before_hook)
        executor.inject_behavior(lambda ctx: "result")
        result = executor.execute("Test")
        assert result.success is False
        assert "Hook error" in result.error

    def test_multiple_executions_same_executor(self):
        """Test multiple executions with same executor."""
        executor = AgentExecutor()
        executor.inject_behavior(lambda ctx: ctx.prompt)
        result1 = executor.execute("First")
        result2 = executor.execute("Second")
        result3 = executor.execute("Third")
        assert result1.output == "First"
        assert result2.output == "Second"
        assert result3.output == "Third"

    def test_reinject_behavior(self):
        """Test reinjecting behavior."""
        executor = AgentExecutor()
        executor.inject_behavior(lambda ctx: "First behavior")
        result1 = executor.execute("Test")
        executor.inject_behavior(lambda ctx: "Second behavior")
        result2 = executor.execute("Test")
        assert result1.output == "First behavior"
        assert result2.output == "Second behavior"
