"""
GAIA Hook Registry and Executor

Registry for hook management and executor for hook execution.
"""

import asyncio
from datetime import datetime
from typing import Dict, List, Any, Optional, Type, Callable
from collections import defaultdict
from dataclasses import dataclass
import threading

from gaia.hooks.base import BaseHook, HookContext, HookResult, HookPriority
from gaia.exceptions import HookRegistrationError, HookExecutionError, HookHaltPipelineError
from gaia.utils.logging import get_logger


logger = get_logger(__name__)


class HookRegistry:
    """
    Registry for hook instances.

    The HookRegistry manages hook registration, organization by event,
    and priority-based sorting for execution ordering.

    Features:
    - Event-based hook organization
    - Priority-based sorting
    - Global hooks (listen to all events)
    - Thread-safe operations

    Example:
        >>> registry = HookRegistry()
        >>> registry.register(MyValidationHook())
        >>> registry.register(MyNotificationHook())
        >>> hooks = registry.get_hooks("AGENT_EXECUTE")
    """

    def __init__(self):
        """Initialize the hook registry."""
        # Hooks organized by event
        self._hooks: Dict[str, List[BaseHook]] = defaultdict(list)
        # Global hooks that listen to all events
        self._global_hooks: List[BaseHook] = []
        # Thread safety
        self._lock = threading.RLock()

        logger.info("HookRegistry initialized")

    def register(self, hook: BaseHook) -> None:
        """
        Register a hook instance.

        Hooks are sorted by priority after registration.
        Global hooks (event="*") are stored separately.

        Args:
            hook: Hook instance to register

        Raises:
            HookRegistrationError: If registration fails
        """
        try:
            with self._lock:
                if hook.event == "*":
                    # Global hook - runs for all events
                    self._global_hooks.append(hook)
                    self._global_hooks.sort(key=lambda h: h.priority.value)
                else:
                    # Event-specific hook
                    self._hooks[hook.event].append(hook)
                    self._hooks[hook.event].sort(key=lambda h: h.priority.value)

            logger.debug(
                f"Registered hook: {hook.name} for event: {hook.event}",
                extra={"hook_name": hook.name, "event": hook.event},
            )

        except Exception as e:
            raise HookRegistrationError(hook.name, str(e))

    def unregister(self, hook_name: str, event: Optional[str] = None) -> bool:
        """
        Unregister a hook by name.

        Args:
            hook_name: Name of hook to remove
            event: Optional event to remove from (removes from all if not specified)

        Returns:
            True if hook was removed, False if not found
        """
        with self._lock:
            removed = False

            # Remove from global hooks
            self._global_hooks = [
                h for h in self._global_hooks
                if h.name != hook_name
            ]

            # Remove from event-specific hooks
            if event:
                if event in self._hooks:
                    before = len(self._hooks[event])
                    self._hooks[event] = [
                        h for h in self._hooks[event]
                        if h.name != hook_name
                    ]
                    removed = len(self._hooks[event]) < before
            else:
                # Remove from all events
                for evt in list(self._hooks.keys()):
                    before = len(self._hooks[evt])
                    self._hooks[evt] = [
                        h for h in self._hooks[evt]
                        if h.name != hook_name
                    ]
                    if len(self._hooks[evt]) < before:
                        removed = True

            return removed

    def get_hooks(self, event: str) -> List[BaseHook]:
        """
        Get all hooks for an event.

        Returns both event-specific hooks and global hooks,
        sorted by priority.

        Args:
            event: Event name

        Returns:
            List of hooks for the event
        """
        with self._lock:
            hooks = list(self._hooks.get(event, []))
            hooks.extend(self._global_hooks)
            return sorted(hooks, key=lambda h: h.priority.value)

    def get_all_hooks(self) -> Dict[str, List[BaseHook]]:
        """
        Get all registered hooks.

        Returns:
            Dictionary of event -> hooks
        """
        with self._lock:
            result = dict(self._hooks)
            result["*"] = list(self._global_hooks)
            return result

    def get_hook_names(self) -> List[str]:
        """Get list of all registered hook names."""
        with self._lock:
            names = set()
            for hooks in self._hooks.values():
                for hook in hooks:
                    names.add(hook.name)
            for hook in self._global_hooks:
                names.add(hook.name)
            return list(names)

    def get_statistics(self) -> Dict[str, Any]:
        """Get registry statistics."""
        with self._lock:
            return {
                "total_hooks": sum(len(h) for h in self._hooks.values()) + len(self._global_hooks),
                "event_hooks": {evt: len(hooks) for evt, hooks in self._hooks.items()},
                "global_hooks": len(self._global_hooks),
                "unique_hook_names": len(self.get_hook_names()),
            }

    def clear(self) -> None:
        """Clear all registered hooks."""
        with self._lock:
            self._hooks.clear()
            self._global_hooks.clear()
        logger.info("HookRegistry cleared")


@dataclass
class HookExecutionRecord:
    """Record of a hook execution."""
    hook_name: str
    event: str
    success: bool
    duration_ms: float
    timestamp: datetime
    error: Optional[str] = None


class HookExecutor:
    """
    Executes hooks for pipeline events.

    The HookExecutor manages hook execution lifecycle:
    1. Retrieve hooks for event
    2. Execute in priority order
    3. Aggregate results
    4. Handle errors
    5. Track execution metrics

    Features:
    - Priority-based execution
    - Blocking/non-blocking hooks
    - Context modification aggregation
    - Error handling and isolation
    - Execution logging

    Example:
        >>> executor = HookExecutor(registry)
        >>> context = HookContext(
        ...     event="AGENT_EXECUTE",
        ...     pipeline_id="test-001",
        ...     data={"task": "Build API"}
        ... )
        >>> result = await executor.execute_hooks("AGENT_EXECUTE", context)
    """

    def __init__(self, registry: HookRegistry):
        """
        Initialize hook executor.

        Args:
            registry: Hook registry instance
        """
        self._registry = registry
        self._execution_log: List[HookExecutionRecord] = []
        self._lock = asyncio.Lock()

        logger.info("HookExecutor initialized")

    async def execute_hooks(
        self,
        event: str,
        context: HookContext,
    ) -> HookResult:
        """
        Execute all hooks for an event.

        Hooks are executed in priority order (HIGH -> NORMAL -> LOW).
        Results are aggregated, and blocking errors halt execution.

        Args:
            event: Event name
            context: Hook context

        Returns:
            Aggregated HookResult
        """
        hooks = self._registry.get_hooks(event)

        if not hooks:
            logger.debug(f"No hooks registered for event: {event}")
            return HookResult(success=True)

        logger.info(
            f"Executing {len(hooks)} hooks for event: {event}",
            extra={"event": event, "hook_count": len(hooks)},
        )

        combined_result = HookResult(success=True)

        for hook in hooks:
            result = await self._execute_single_hook(hook, context)
            combined_result = self._aggregate_results(combined_result, result, hook)

            # Check if should halt
            if result.halt_pipeline or (not result.success and hook.blocking):
                logger.warning(
                    f"Halting pipeline due to hook: {hook.name}",
                    extra={"hook_name": hook.name},
                )
                break

        # Log execution summary
        async with self._lock:
            self._execution_log.append(HookExecutionRecord(
                hook_name="*",
                event=event,
                success=combined_result.success,
                duration_ms=0,  # Would track in production
                timestamp=datetime.utcnow(),
            ))

        return combined_result

    async def _execute_single_hook(
        self,
        hook: BaseHook,
        context: HookContext,
    ) -> HookResult:
        """
        Execute a single hook with error handling.

        Args:
            hook: Hook to execute
            context: Hook context

        Returns:
            HookResult from execution
        """
        start_time = datetime.utcnow()
        result = HookResult(success=True)

        try:
            # Before hook
            await hook.on_before(context)

            # Execute hook
            hook._increment_execution()
            result = await hook.execute(context)

            # After hook
            await hook.on_after(context, result)

        except Exception as e:
            logger.exception(
                f"Hook execution error: {hook.name}",
                extra={"hook_name": hook.name, "event": context.event},
            )
            hook._set_error(str(e))
            result = HookResult(
                success=False,
                blocking=hook.blocking,
                error_message=str(e),
                halt_pipeline=hook.blocking,
            )

        # Record execution
        duration = (datetime.utcnow() - start_time).total_seconds() * 1000
        async with self._lock:
            self._execution_log.append(HookExecutionRecord(
                hook_name=hook.name,
                event=context.event,
                success=result.success,
                duration_ms=duration,
                timestamp=datetime.utcnow(),
                error=result.error_message,
            ))

        return result

    def _aggregate_results(
        self,
        current: HookResult,
        new: HookResult,
        hook: BaseHook,
    ) -> HookResult:
        """
        Aggregate multiple hook results.

        Success is True only if all hooks succeeded.
        Data modifications and context injections are merged.

        Args:
            current: Current aggregated result
            new: New hook result to aggregate
            hook: Hook that produced the new result

        Returns:
            Aggregated HookResult
        """
        # Success is True only if all hooks succeeded
        aggregated = HookResult(
            success=current.success and new.success,
            blocking=current.blocking or new.blocking,
            halt_pipeline=current.halt_pipeline or new.halt_pipeline,
            defects=current.defects + new.defects,
            metadata={**current.metadata, **new.metadata},
        )

        # Merge data modifications (later hooks override earlier)
        if current.modify_data and new.modify_data:
            aggregated.modify_data = {**current.modify_data, **new.modify_data}
        elif new.modify_data:
            aggregated.modify_data = new.modify_data
        else:
            aggregated.modify_data = current.modify_data

        # Merge context injections
        if current.inject_context and new.inject_context:
            aggregated.inject_context = {**current.inject_context, **new.inject_context}
        elif new.inject_context:
            aggregated.inject_context = new.inject_context
        else:
            aggregated.inject_context = current.inject_context

        # Keep first error message
        if not aggregated.error_message:
            aggregated.error_message = new.error_message

        return aggregated

    def get_execution_log(self) -> List[HookExecutionRecord]:
        """Get execution log."""
        return list(self._execution_log)

    def get_execution_summary(self) -> Dict[str, Any]:
        """Get execution summary."""
        total = len(self._execution_log)
        successful = sum(1 for r in self._execution_log if r.success)
        failed = total - successful

        return {
            "total_executions": total,
            "successful": successful,
            "failed": failed,
            "success_rate": successful / total * 100 if total > 0 else 100.0,
            "unique_hooks": len(set(r.hook_name for r in self._execution_log)),
            "unique_events": len(set(r.event for r in self._execution_log)),
        }

    def clear_log(self) -> None:
        """Clear execution log."""
        self._execution_log.clear()


# Import dataclass for type hints
from dataclasses import dataclass
