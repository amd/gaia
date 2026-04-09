"""
GAIA Pipeline Metrics Hooks

Hook implementations for automatic metrics capture during pipeline execution.

This module provides hooks that automatically capture metrics at key points:
- PHASE_ENTER: Start timing phases
- PHASE_EXIT: Record phase duration and outcomes
- LOOP_START: Initialize loop metrics tracking
- LOOP_END: Record loop iteration metrics
- QUALITY_EVAL: Capture quality scores
- AGENT_SELECT: Track agent selection decisions
- HOOK_EXECUTE: Record hook execution times
"""

import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from gaia.hooks.base import BaseHook, HookContext, HookPriority, HookResult
from gaia.pipeline.metrics_collector import (
    PipelineMetricsCollector,
    get_pipeline_collector,
)
from gaia.utils.logging import get_logger

logger = get_logger(__name__)


class PhaseEnterMetricsHook(BaseHook):
    """
    Records metrics when entering a pipeline phase.

    This hook:
    - Starts phase timing
    - Records state transition
    - Captures initial resource utilization
    """

    name = "phase_enter_metrics"
    event = "PHASE_ENTER"
    priority = HookPriority.HIGH
    blocking = False
    description = "Records metrics at phase entry"

    def __init__(self, collector: Optional[PipelineMetricsCollector] = None):
        """
        Initialize hook.

        Args:
            collector: Optional metrics collector instance
        """
        super().__init__()
        self._collector = collector

    async def execute(self, context: HookContext) -> HookResult:
        """
        Execute phase enter metrics capture.

        Args:
            context: Hook context

        Returns:
            HookResult with execution outcome
        """
        pipeline_id = context.pipeline_id
        phase = context.phase

        # Get or create collector
        if not self._collector:
            self._collector = get_pipeline_collector(pipeline_id)

        # Start phase timing
        self._collector.start_phase(phase)

        # Record state transition
        previous_phase = context.metadata.get("previous_phase", "INIT")
        self._collector.record_state_transition(
            from_state=previous_phase,
            to_state=phase,
            reason="Phase transition",
            metadata={"pipeline_id": pipeline_id},
        )

        logger.debug(
            f"Phase enter metrics recorded: {phase}",
            extra={"pipeline_id": pipeline_id, "phase": phase},
        )

        return HookResult.success_result(
            metadata={
                "phase_started": phase,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )


class PhaseExitMetricsHook(BaseHook):
    """
    Records metrics when exiting a pipeline phase.

    This hook:
    - Ends phase timing
    - Records phase duration
    - Captures outcome metrics
    """

    name = "phase_exit_metrics"
    event = "PHASE_EXIT"
    priority = HookPriority.HIGH
    blocking = False
    description = "Records metrics at phase exit"

    def __init__(self, collector: Optional[PipelineMetricsCollector] = None):
        """
        Initialize hook.

        Args:
            collector: Optional metrics collector instance
        """
        super().__init__()
        self._collector = collector

    async def execute(self, context: HookContext) -> HookResult:
        """
        Execute phase exit metrics capture.

        Args:
            context: Hook context

        Returns:
            HookResult with execution outcome
        """
        pipeline_id = context.pipeline_id
        phase = context.phase
        success = context.data.get("success", True)

        # Get or create collector
        if not self._collector:
            self._collector = get_pipeline_collector(pipeline_id)

        # End phase timing
        self._collector.end_phase(phase)

        # Record outcome
        self._collector.record_state_transition(
            from_state=phase,
            to_state="COMPLETED" if success else "FAILED",
            reason="Phase exit",
            metadata={"success": success},
        )

        logger.debug(
            f"Phase exit metrics recorded: {phase} (success={success})",
            extra={"pipeline_id": pipeline_id, "phase": phase},
        )

        return HookResult.success_result(
            metadata={"phase_ended": phase, "success": success}
        )


class LoopStartMetricsHook(BaseHook):
    """
    Records metrics when starting a loop iteration.

    This hook:
    - Initializes loop metrics tracking
    - Records loop iteration count
    """

    name = "loop_start_metrics"
    event = "LOOP_START"
    priority = HookPriority.NORMAL
    blocking = False
    description = "Records metrics at loop start"

    def __init__(self, collector: Optional[PipelineMetricsCollector] = None):
        """
        Initialize hook.

        Args:
            collector: Optional metrics collector instance
        """
        super().__init__()
        self._collector = collector

    async def execute(self, context: HookContext) -> HookResult:
        """
        Execute loop start metrics capture.

        Args:
            context: Hook context

        Returns:
            HookResult with execution outcome
        """
        pipeline_id = context.pipeline_id
        loop_id = context.loop_id
        phase = context.phase

        # Get or create collector
        if not self._collector:
            self._collector = get_pipeline_collector(pipeline_id)

        # Record loop iteration
        iteration = self._collector.record_loop_iteration(
            loop_id=loop_id,
            phase_name=phase or "UNKNOWN",
        )

        logger.debug(
            f"Loop start metrics recorded: {loop_id} (iteration {iteration})",
            extra={"pipeline_id": pipeline_id, "loop_id": loop_id},
        )

        return HookResult.success_result(
            metadata={"loop_id": loop_id, "iteration": iteration}
        )


class LoopEndMetricsHook(BaseHook):
    """
    Records metrics when ending a loop iteration.

    This hook:
    - Records final loop metrics
    - Captures quality score if available
    - Tracks defects discovered
    """

    name = "loop_end_metrics"
    event = "LOOP_END"
    priority = HookPriority.NORMAL
    blocking = False
    description = "Records metrics at loop end"

    def __init__(self, collector: Optional[PipelineMetricsCollector] = None):
        """
        Initialize hook.

        Args:
            collector: Optional metrics collector instance
        """
        super().__init__()
        self._collector = collector

    async def execute(self, context: HookContext) -> HookResult:
        """
        Execute loop end metrics capture.

        Args:
            context: Hook context

        Returns:
            HookResult with execution outcome
        """
        pipeline_id = context.pipeline_id
        loop_id = context.loop_id
        phase = context.phase

        # Get or create collector
        if not self._collector:
            self._collector = get_pipeline_collector(pipeline_id)

        # Record quality score if available
        quality_score = context.data.get("quality_score")
        if quality_score is not None:
            self._collector.record_quality_score(
                loop_id=loop_id,
                phase_name=phase or "UNKNOWN",
                quality_score=quality_score,
            )

        # Record defects if available
        defects = context.data.get("defects", [])
        for defect in defects:
            defect_type = defect.get("category", "unknown")
            self._collector.record_defect(
                loop_id=loop_id,
                phase_name=phase or "UNKNOWN",
                defect_type=defect_type,
            )

        logger.debug(
            f"Loop end metrics recorded: {loop_id}",
            extra={"pipeline_id": pipeline_id, "loop_id": loop_id},
        )

        return HookResult.success_result(
            metadata={
                "loop_id": loop_id,
                "quality_score": quality_score,
                "defect_count": len(defects),
            }
        )


class QualityEvalMetricsHook(BaseHook):
    """
    Records metrics during quality evaluation.

    This hook:
    - Captures quality scores
    - Records evaluation duration
    - Tracks evaluation outcomes
    """

    name = "quality_eval_metrics"
    event = "QUALITY_EVAL"
    priority = HookPriority.NORMAL
    blocking = False
    description = "Records metrics during quality evaluation"

    def __init__(self, collector: Optional[PipelineMetricsCollector] = None):
        """
        Initialize hook.

        Args:
            collector: Optional metrics collector instance
        """
        super().__init__()
        self._collector = collector

    async def execute(self, context: HookContext) -> HookResult:
        """
        Execute quality eval metrics capture.

        Args:
            context: Hook context

        Returns:
            HookResult with execution outcome
        """
        pipeline_id = context.pipeline_id
        loop_id = context.loop_id
        phase = context.phase

        # Get or create collector
        if not self._collector:
            self._collector = get_pipeline_collector(pipeline_id)

        # Record quality score
        quality_score = context.data.get("quality_score")
        if quality_score is not None:
            self._collector.record_quality_score(
                loop_id=loop_id or pipeline_id,
                phase_name=phase or "QUALITY",
                quality_score=quality_score,
            )

        logger.debug(
            f"Quality eval metrics recorded: score={quality_score}",
            extra={"pipeline_id": pipeline_id, "quality_score": quality_score},
        )

        return HookResult.success_result(
            metadata={"quality_score": quality_score, "loop_id": loop_id}
        )


class AgentSelectMetricsHook(BaseHook):
    """
    Records metrics when selecting an agent.

    This hook:
    - Tracks agent selection decisions
    - Records selection rationale
    - Captures alternative agents considered
    """

    name = "agent_select_metrics"
    event = "AGENT_SELECT"
    priority = HookPriority.NORMAL
    blocking = False
    description = "Records metrics during agent selection"

    def __init__(self, collector: Optional[PipelineMetricsCollector] = None):
        """
        Initialize hook.

        Args:
            collector: Optional metrics collector instance
        """
        super().__init__()
        self._collector = collector

    async def execute(self, context: HookContext) -> HookResult:
        """
        Execute agent select metrics capture.

        Args:
            context: Hook context

        Returns:
            HookResult with execution outcome
        """
        pipeline_id = context.pipeline_id
        phase = context.phase

        # Get or create collector
        if not self._collector:
            self._collector = get_pipeline_collector(pipeline_id)

        # Get selection details from context
        agent_id = context.data.get("agent_id")
        reason = context.data.get("reason", "Default selection")
        alternatives = context.data.get("alternatives", [])

        if agent_id:
            self._collector.record_agent_selection(
                phase_name=phase or "UNKNOWN",
                agent_id=agent_id,
                reason=reason,
                alternatives=alternatives,
            )

        logger.debug(
            f"Agent selection metrics recorded: {agent_id}",
            extra={"pipeline_id": pipeline_id, "agent_id": agent_id},
        )

        return HookResult.success_result(
            metadata={"agent_id": agent_id, "phase": phase}
        )


class HookExecutionMetricsHook(BaseHook):
    """
    Records metrics for hook execution times.

    This hook wraps other hooks to measure their execution duration.
    It uses timing hooks around the actual hook execution.
    """

    name = "hook_execution_metrics"
    event = "*"  # Listen to all events
    priority = HookPriority.LOW
    blocking = False
    description = "Records hook execution timing"

    def __init__(self, collector: Optional[PipelineMetricsCollector] = None):
        """
        Initialize hook.

        Args:
            collector: Optional metrics collector instance
        """
        super().__init__()
        self._collector = collector
        self._timing_contexts: Dict[str, float] = {}

    async def execute(self, context: HookContext) -> HookResult:
        """
        Execute hook execution timing capture.

        Args:
            context: Hook context

        Returns:
            HookResult with execution outcome
        """
        pipeline_id = context.pipeline_id
        event = context.event

        # Get or create collector
        if not self._collector:
            self._collector = get_pipeline_collector(pipeline_id)

        # Get hook name from context data
        hook_name = context.data.get("hook_name", "unknown")

        # Record execution time if available
        execution_time = context.data.get("execution_time_seconds")
        success = context.data.get("hook_success", True)

        if execution_time is not None:
            self._collector.record_hook_execution(
                hook_name=hook_name,
                event=event,
                duration_seconds=execution_time,
                success=success,
            )

        logger.debug(
            f"Hook execution metrics recorded: {hook_name} ({execution_time}s)",
            extra={"pipeline_id": pipeline_id, "hook_name": hook_name},
        )

        return HookResult.success_result(
            metadata={"hook_name": hook_name, "execution_time": execution_time}
        )


class TimingHookWrapper:
    """
    Wrapper for timing hook execution.

    This is a utility class that wraps hook execution to capture timing
    metrics automatically.
    """

    def __init__(self, collector: Optional[PipelineMetricsCollector] = None):
        """
        Initialize timing wrapper.

        Args:
            collector: Optional metrics collector instance
        """
        self._collector = collector

    def wrap_hook(
        self,
        hook: BaseHook,
        context: HookContext,
    ) -> Callable:
        """
        Create a wrapped version of hook.execute that records timing.

        Args:
            hook: Hook to wrap
            context: Hook context

        Returns:
            Wrapped async function
        """
        original_execute = hook.execute

        async def timed_execute(ctx: HookContext) -> HookResult:
            start_time = time.perf_counter()
            result = None
            success = True
            try:
                result = await original_execute(ctx)
                success = result.success
                return result
            except Exception:
                success = False
                raise
            finally:
                end_time = time.perf_counter()
                duration = end_time - start_time

                # Record timing
                if self._collector:
                    self._collector.record_hook_execution(
                        hook_name=hook.name,
                        event=ctx.event,
                        duration_seconds=duration,
                        success=success,
                    )

        return timed_execute


def create_metrics_hook_group(
    collector: Optional[PipelineMetricsCollector] = None,
) -> List[BaseHook]:
    """
    Create a group of metrics hooks for pipeline instrumentation.

    Args:
        collector: Optional metrics collector instance

    Returns:
        List of metrics hooks ready for registration
    """
    return [
        PhaseEnterMetricsHook(collector),
        PhaseExitMetricsHook(collector),
        LoopStartMetricsHook(collector),
        LoopEndMetricsHook(collector),
        QualityEvalMetricsHook(collector),
        AgentSelectMetricsHook(collector),
        HookExecutionMetricsHook(collector),
    ]


def register_metrics_hooks(
    hook_registry: Any,
    collector: Optional[PipelineMetricsCollector] = None,
) -> int:
    """
    Register all metrics hooks with a hook registry.

    Args:
        hook_registry: HookRegistry instance
        collector: Optional metrics collector instance

    Returns:
        Number of hooks registered
    """
    hooks = create_metrics_hook_group(collector)
    for hook in hooks:
        hook_registry.register(hook)
    return len(hooks)
