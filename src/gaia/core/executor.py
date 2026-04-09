# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Agent Executor for the modular architecture core.

This module provides the AgentExecutor class that runs agents with injected
behavior, lifecycle hooks, and error recovery strategies.

Key Components:
    - AgentExecutor: Main executor class for running agents
    - Execution hooks (before, after, error)
    - Behavior injection mechanism
    - Error handling and recovery strategies
    - Async/await support

Example Usage:
    ```python
    from gaia.core.executor import AgentExecutor
    from gaia.core.profile import AgentProfile

    # Create executor with a profile
    profile = AgentProfile(name="Test Agent")
    executor = AgentExecutor(profile=profile)

    # Add lifecycle hooks
    def before_hook(context):
        print(f"Before execution: {context}")

    def after_hook(context, result):
        print(f"After execution: {result}")

    executor.set_before_hook(before_hook)
    executor.set_after_hook(after_hook)

    # Execute with context
    result = executor.execute("Test prompt", {"key": "value"})
    ```
"""

import asyncio
import threading
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Union

# Module-level asyncio import for compatibility with async operations
# This ensures asyncio is available for async hooks and behavior execution

from gaia.core.capabilities import AgentCapabilities
from gaia.core.profile import AgentProfile
from gaia.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class ExecutionContext:
    """
    Context information for agent execution.

    This dataclass captures all relevant context for an agent execution,
    including the prompt, additional context data, and execution state.

    Attributes:
        prompt: The main prompt or instruction for the agent.
        context: Additional context data dictionary.
        execution_id: Unique identifier for this execution.
        metadata: Additional metadata dictionary.
    """

    prompt: str
    context: Dict[str, Any] = field(default_factory=dict)
    execution_id: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        """Initialize execution_id if not provided."""
        import uuid

        if not self.execution_id:
            self.execution_id = str(uuid.uuid4())
        self.context = dict(self.context) if self.context else {}
        self.metadata = dict(self.metadata) if self.metadata else {}


@dataclass
class ExecutionResult:
    """
    Result of an agent execution.

    This dataclass captures the result of an agent execution,
    including success status, output data, and any errors.

    Attributes:
        success: Whether the execution succeeded.
        output: Output data from the execution.
        error: Error message if execution failed.
        execution_id: Unique identifier linking to ExecutionContext.
        metadata: Additional metadata dictionary.
    """

    success: bool = False
    output: Any = None
    error: Optional[str] = None
    execution_id: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        """Ensure metadata is a copy."""
        self.metadata = dict(self.metadata) if self.metadata else {}

    def to_dict(self) -> Dict[str, Any]:
        """Convert result to dictionary."""
        return {
            "success": self.success,
            "output": self.output,
            "error": self.error,
            "execution_id": self.execution_id,
            "metadata": dict(self.metadata),
        }


# Type alias for behavior functions
BehaviorFn = Callable[[ExecutionContext], Union[Any, Awaitable[Any]]]
HookFn = Callable[..., Any]


class AgentExecutor:
    """
    Executor for running agents with injected behavior.

    This class provides a flexible execution framework for agents,
    supporting dynamic behavior injection, lifecycle hooks, and
    comprehensive error handling.

    Thread Safety:
        All public methods are thread-safe using RLock for reentrant locking.
        Deep copies are used for mutable state to prevent unintended mutations.

    Attributes:
        profile: AgentProfile for this executor.
        default_behavior: Default behavior function if no behavior injected.

    Example:
        >>> from gaia.core.profile import AgentProfile
        >>> profile = AgentProfile(name="Test Agent")
        >>> executor = AgentExecutor(profile=profile)
        >>>
        >>> def my_behavior(ctx):
        ...     return f"Processed: {ctx.prompt}"
        >>>
        >>> executor.inject_behavior(my_behavior)
        >>> result = executor.execute("Hello")
        >>> result.output
        'Processed: Hello'
    """

    def __init__(
        self,
        profile: Optional[AgentProfile] = None,
        default_behavior: Optional[BehaviorFn] = None,
    ):
        """
        Initialize the agent executor.

        Args:
            profile: AgentProfile for this executor. If None, creates default.
            default_behavior: Default behavior function if no behavior injected.
        """
        self._lock = threading.RLock()
        self.profile = profile or AgentProfile()
        self._behavior: Optional[BehaviorFn] = default_behavior
        self._before_hook: Optional[HookFn] = None
        self._after_hook: Optional[HookFn] = None
        self._error_handler: Optional[HookFn] = None
        self._error_recovery_strategy: str = "raise"  # raise, return_default, retry
        self._max_retries: int = 3
        self._retry_delay: float = 1.0  # seconds
        self._execution_history: List[Dict[str, Any]] = []
        self._max_history: int = 100

        logger.info(
            f"AgentExecutor initialized for profile: {profile.name if profile else 'default'}"
        )

    def inject_behavior(self, behavior_fn: BehaviorFn) -> None:
        """
        Inject a behavior function for agent execution.

        This method sets the primary behavior that will be executed when
        execute() is called. The behavior function receives the ExecutionContext
        and returns the result.

        Args:
            behavior_fn: Function to execute. Can be sync or async.

        Example:
            >>> executor = AgentExecutor()
            >>> def my_behavior(ctx):
            ...     return f"Processed: {ctx.prompt}"
            >>> executor.inject_behavior(my_behavior)
        """
        with self._lock:
            self._behavior = behavior_fn
            logger.debug("Behavior injected successfully")

    def set_before_hook(self, hook_fn: HookFn) -> None:
        """
        Set a hook to run before agent execution.

        The before hook receives the ExecutionContext and can modify it
        or perform setup operations.

        Args:
            hook_fn: Hook function to run before execution.

        Example:
            >>> def before_hook(ctx):
            ...     print(f"Executing: {ctx.prompt}")
            >>> executor.set_before_hook(before_hook)
        """
        with self._lock:
            self._before_hook = hook_fn
            logger.debug("Before hook set successfully")

    def set_after_hook(self, hook_fn: HookFn) -> None:
        """
        Set a hook to run after successful agent execution.

        The after hook receives the ExecutionContext and ExecutionResult,
        and can modify the result or perform cleanup operations.

        Args:
            hook_fn: Hook function to run after execution.

        Example:
            >>> def after_hook(ctx, result):
            ...     print(f"Result: {result.output}")
            >>> executor.set_after_hook(after_hook)
        """
        with self._lock:
            self._after_hook = hook_fn
            logger.debug("After hook set successfully")

    def set_error_handler(
        self,
        handler_fn: HookFn,
        recovery_strategy: str = "raise",
        max_retries: int = 3,
        retry_delay: float = 1.0,
    ) -> None:
        """
        Set error handler and recovery strategy.

        Args:
            handler_fn: Error handler function.
            recovery_strategy: One of "raise", "return_default", "retry".
            max_retries: Maximum retry attempts (for "retry" strategy).
            retry_delay: Delay between retries in seconds.

        Example:
            >>> def error_handler(ctx, error):
            ...     print(f"Error: {error}")
            >>> executor.set_error_handler(error_handler, recovery_strategy="retry")
        """
        with self._lock:
            self._error_handler = handler_fn
            self._error_recovery_strategy = recovery_strategy
            self._max_retries = max_retries
            self._retry_delay = retry_delay
            logger.debug(
                f"Error handler set with strategy: {recovery_strategy}, "
                f"max_retries: {max_retries}"
            )

    def execute(
        self,
        prompt: str,
        context: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ExecutionResult:
        """
        Execute the agent with the given prompt and context.

        This is the main execution method. It runs the before hook,
        executes the injected behavior, runs the after hook, and
        handles any errors according to the recovery strategy.

        Args:
            prompt: The main prompt or instruction.
            context: Additional context data.
            metadata: Additional metadata.

        Returns:
            ExecutionResult with success status and output.

        Example:
            >>> executor = AgentExecutor()
            >>> executor.inject_behavior(lambda ctx: f"Result: {ctx.prompt}")
            >>> result = executor.execute("Hello")
            >>> result.success
            True
            >>> result.output
            'Result: Hello'
        """
        import uuid

        with self._lock:
            # Create execution context
            exec_context = ExecutionContext(
                prompt=prompt,
                context=context or {},
                execution_id=str(uuid.uuid4()),
                metadata=metadata or {},
            )

            # Initialize result
            result = ExecutionResult(execution_id=exec_context.execution_id)

            try:
                # Run before hook
                if self._before_hook:
                    logger.debug(f"Running before hook for {exec_context.execution_id}")
                    hook_result = self._before_hook(exec_context)
                    if asyncio.iscoroutine(hook_result):
                        # For sync execute, we need to handle async hooks
                        logger.warning(
                            "Async before hook called from sync execute - "
                            "consider using execute_async()"
                        )
                        # Run in event loop
                        try:
                            loop = asyncio.get_event_loop()
                            if loop.is_running():
                                # Schedule and wait (not ideal, but works)
                                future = asyncio.run_coroutine_threadsafe(
                                    hook_result, loop
                                )
                                future.result(timeout=30)
                            else:
                                loop.run_until_complete(hook_result)
                        except RuntimeError:
                            # No event loop, run new one
                            asyncio.run(hook_result)

                # Check if behavior is set
                if self._behavior is None:
                    raise ValueError("No behavior injected. Call inject_behavior() first.")

                # Execute behavior with retry logic
                attempt = 0
                last_error = None

                while attempt <= self._max_retries:
                    try:
                        logger.debug(
                            f"Executing behavior (attempt {attempt + 1}/"
                            f"{self._max_retries + 1})"
                        )
                        behavior_result = self._behavior(exec_context)

                        # Handle async behavior
                        if asyncio.iscoroutine(behavior_result):
                            logger.warning(
                                "Async behavior called from sync execute - "
                                "consider using execute_async()"
                            )
                            try:
                                loop = asyncio.get_event_loop()
                                if loop.is_running():
                                    future = asyncio.run_coroutine_threadsafe(
                                        behavior_result, loop
                                    )
                                    behavior_result = future.result(timeout=300)
                                else:
                                    behavior_result = loop.run_until_complete(
                                        behavior_result
                                    )
                            except RuntimeError:
                                behavior_result = asyncio.run(behavior_result)

                        # Create result
                        result.success = True
                        result.output = behavior_result

                        # Run after hook
                        if self._after_hook:
                            logger.debug(f"Running after hook for {exec_context.execution_id}")
                            self._after_hook(exec_context, result)

                        # Record success in history
                        self._record_execution(exec_context, result)
                        return result

                    except Exception as e:
                        last_error = e
                        attempt += 1
                        logger.warning(
                            f"Behavior execution failed (attempt {attempt}/"
                            f"{self._max_retries + 1}): {e}"
                        )

                        # Run error handler
                        if self._error_handler:
                            try:
                                self._error_handler(exec_context, e)
                            except Exception as handler_error:
                                logger.error(f"Error handler failed: {handler_error}")

                        # Check recovery strategy
                        if self._error_recovery_strategy == "raise":
                            if attempt > self._max_retries:
                                raise
                        elif self._error_recovery_strategy == "retry":
                            if attempt <= self._max_retries:
                                import time

                                time.sleep(self._retry_delay)
                                continue
                            else:
                                raise
                        elif self._error_recovery_strategy == "return_default":
                            result.success = False
                            result.error = str(e)
                            self._record_execution(exec_context, result)
                            return result

                # If we get here, all retries exhausted
                result.success = False
                result.error = str(last_error)
                self._record_execution(exec_context, result)
                return result

            except Exception as e:
                logger.exception(f"Execution failed: {e}")
                result.success = False
                result.error = str(e)
                self._record_execution(exec_context, result)
                return result

    async def execute_async(
        self,
        prompt: str,
        context: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ExecutionResult:
        """
        Execute the agent asynchronously.

        This is the async version of execute(). It properly handles
        async behavior functions and hooks.

        Args:
            prompt: The main prompt or instruction.
            context: Additional context data.
            metadata: Additional metadata.

        Returns:
            ExecutionResult with success status and output.

        Example:
            >>> executor = AgentExecutor()
            >>> async def my_behavior(ctx):
            ...     await asyncio.sleep(1)
            ...     return f"Result: {ctx.prompt}"
            >>> executor.inject_behavior(my_behavior)
            >>> result = await executor.execute_async("Hello")
            >>> result.success
            True
        """
        import uuid

        with self._lock:
            # Create execution context
            exec_context = ExecutionContext(
                prompt=prompt,
                context=context or {},
                execution_id=str(uuid.uuid4()),
                metadata=metadata or {},
            )

            # Initialize result
            result = ExecutionResult(execution_id=exec_context.execution_id)

            try:
                # Run before hook (async-safe)
                if self._before_hook:
                    logger.debug(f"Running before hook for {exec_context.execution_id}")
                    hook_result = self._before_hook(exec_context)
                    if asyncio.iscoroutine(hook_result):
                        await hook_result

                # Check if behavior is set
                if self._behavior is None:
                    raise ValueError("No behavior injected. Call inject_behavior() first.")

                # Execute behavior with retry logic
                attempt = 0
                last_error = None

                while attempt <= self._max_retries:
                    try:
                        logger.debug(
                            f"Executing behavior (attempt {attempt + 1}/"
                            f"{self._max_retries + 1})"
                        )
                        behavior_result = self._behavior(exec_context)

                        # Handle async behavior
                        if asyncio.iscoroutine(behavior_result):
                            behavior_result = await behavior_result

                        # Create result
                        result.success = True
                        result.output = behavior_result

                        # Run after hook (async-safe)
                        if self._after_hook:
                            logger.debug(f"Running after hook for {exec_context.execution_id}")
                            after_result = self._after_hook(exec_context, result)
                            if asyncio.iscoroutine(after_result):
                                await after_result

                        # Record success in history
                        self._record_execution(exec_context, result)
                        return result

                    except Exception as e:
                        last_error = e
                        attempt += 1
                        logger.warning(
                            f"Behavior execution failed (attempt {attempt}/"
                            f"{self._max_retries + 1}): {e}"
                        )

                        # Run error handler (async-safe)
                        if self._error_handler:
                            try:
                                handler_result = self._error_handler(exec_context, e)
                                if asyncio.iscoroutine(handler_result):
                                    await handler_result
                            except Exception as handler_error:
                                logger.error(f"Error handler failed: {handler_error}")

                        # Check recovery strategy
                        if self._error_recovery_strategy == "raise":
                            if attempt > self._max_retries:
                                raise
                        elif self._error_recovery_strategy == "retry":
                            if attempt <= self._max_retries:
                                await asyncio.sleep(self._retry_delay)
                                continue
                            else:
                                raise
                        elif self._error_recovery_strategy == "return_default":
                            result.success = False
                            result.error = str(e)
                            self._record_execution(exec_context, result)
                            return result

                # If we get here, all retries exhausted
                result.success = False
                result.error = str(last_error)
                self._record_execution(exec_context, result)
                return result

            except Exception as e:
                logger.exception(f"Execution failed: {e}")
                result.success = False
                result.error = str(e)
                self._record_execution(exec_context, result)
                return result

    def _record_execution(
        self, context: ExecutionContext, result: ExecutionResult
    ) -> None:
        """Record execution in history."""
        import time

        with self._lock:
            record = {
                "execution_id": context.execution_id,
                "prompt": context.prompt,
                "success": result.success,
                "timestamp": time.time(),
            }
            self._execution_history.append(record)

            # Trim history if needed
            while len(self._execution_history) > self._max_history:
                self._execution_history.pop(0)

    def get_execution_history(self) -> List[Dict[str, Any]]:
        """
        Get the execution history.

        Returns:
            List of execution records.
        """
        with self._lock:
            return list(self._execution_history)

    def clear_execution_history(self) -> None:
        """Clear the execution history."""
        with self._lock:
            self._execution_history.clear()

    def get_capabilities(self) -> AgentCapabilities:
        """
        Get the capabilities from the profile.

        Returns:
            AgentCapabilities instance.
        """
        with self._lock:
            if self.profile and self.profile.capabilities:
                # Use to_dict/from_dict to avoid pickle issues with RLock
                caps_dict = self.profile.capabilities.to_dict()
                return AgentCapabilities.from_dict(caps_dict)
            return AgentCapabilities()

    def get_status(self) -> Dict[str, Any]:
        """
        Get executor status information.

        Returns:
            Dictionary with status information.
        """
        with self._lock:
            return {
                "profile_name": self.profile.name if self.profile else None,
                "has_behavior": self._behavior is not None,
                "has_before_hook": self._before_hook is not None,
                "has_after_hook": self._after_hook is not None,
                "has_error_handler": self._error_handler is not None,
                "error_recovery_strategy": self._error_recovery_strategy,
                "max_retries": self._max_retries,
                "execution_count": len(self._execution_history),
            }

    def __repr__(self) -> str:
        """Return string representation of executor."""
        status = self.get_status()
        return (
            f"AgentExecutor(profile='{status['profile_name']}', "
            f"behavior={'set' if status['has_behavior'] else 'none'}, "
            f"executions={status['execution_count']})"
        )
