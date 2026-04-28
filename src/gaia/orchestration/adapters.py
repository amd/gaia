"""
OrchestratorPipelineAdapter — Architectural boundary between the
ProjectOrchestrator and PipelineEngine.

The adapter is responsible for:
1. Mapping Objective data to PipelineEngine configuration
2. Executing PipelineEngine for a single objective
3. Translating execution results back to Objective updates
4. Bridging async/sync boundaries where needed

This module does NOT modify PipelineEngine — it is a pure consumer.
"""

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from gaia.exceptions import (
    PipelineAlreadyRunningError,
    PipelineNotInitializedError,
)
from gaia.hooks.base import HookContext, HookResult
from gaia.pipeline.engine import PipelineConfig, PipelineEngine
from gaia.pipeline.state import PipelineContext, PipelineState
from gaia.resilience import CircuitBreaker, CircuitBreakerConfig
from gaia.utils.logging import get_logger

from gaia.orchestration.models import Artifact, Objective, ObjectiveStatus

logger = get_logger(__name__)


@dataclass
class ExecutionResult:
    """
    Result of executing an objective through the pipeline.

    Attributes:
        success: Whether execution succeeded
        objective_id: The objective that was executed
        pipeline_snapshot: Raw PipelineSnapshot from PipelineEngine
        artifacts: Artifacts produced by the execution
        quality_score: Quality score if available
        error_message: Error description if failed
    """

    success: bool
    objective_id: str
    pipeline_snapshot: Optional[Any] = None
    artifacts: list = field(default_factory=list)
    quality_score: Optional[float] = None
    error_message: Optional[str] = None


class OrchestratorPipelineAdapter:
    """
    Adapts PipelineEngine for orchestrator consumption.

    This adapter establishes a clean architectural boundary:
    - The orchestrator interacts only with this adapter
    - PipelineEngine remains untouched and unaware of orchestration
    - Objective lifecycle is mapped to PipelineEngine execution lifecycle

    The adapter provides resilience via CircuitBreaker to prevent
    cascading failures when PipelineEngine is unavailable.

    CircuitBreaker usage:
        The correct pattern is:
            wrapped = self._circuit_breaker(some_func)  # returns wrapper
            result = wrapped(*args, **kwargs)             # actually call wrapper
        Or equivalently:
            result = self._circuit_breaker.call(some_func, *args)

    Example:
        >>> adapter = OrchestratorPipelineAdapter()
        >>> result = await adapter.execute(objective)
        >>> if result.success:
        ...     objective.transition_to(ObjectiveStatus.COMPLETED)
    """

    def __init__(
        self,
        pipeline_engine: Optional[PipelineEngine] = None,
        enable_circuit_breaker: bool = True,
        circuit_breaker_config: Optional[CircuitBreakerConfig] = None,
    ) -> None:
        """
        Initialize the adapter.

        Args:
            pipeline_engine: Optional PipelineEngine instance. If None,
                a new instance is created on first use.
            enable_circuit_breaker: Whether to wrap calls in CircuitBreaker
            circuit_breaker_config: Custom circuit breaker configuration
        """
        self._pipeline_engine = pipeline_engine
        self._enable_circuit_breaker = enable_circuit_breaker
        self._circuit_breaker = CircuitBreaker(
            config=circuit_breaker_config
            or CircuitBreakerConfig(
                failure_threshold=5,
                recovery_timeout=30.0,
                success_threshold=2,
            )
        )
        logger.info(
            "OrchestratorPipelineAdapter initialized",
            extra={"circuit_breaker": enable_circuit_breaker},
        )

    @property
    def pipeline_engine(self) -> PipelineEngine:
        """Get or create the PipelineEngine instance."""
        if self._pipeline_engine is None:
            self._pipeline_engine = PipelineEngine(
                enable_logging=False,
                skip_lemonade=True,
            )
        return self._pipeline_engine

    def _build_pipeline_context(self, objective: Objective) -> PipelineContext:
        """
        Map an Objective to a PipelineContext.

        Args:
            objective: The objective to execute

        Returns:
            PipelineConfigured for this objective
        """
        pipeline_config = objective.pipeline_config or {}
        return PipelineContext(
            pipeline_id=f"orch-{objective.objective_id}-{uuid.uuid4().hex[:8]}",
            user_goal=objective.description or objective.title,
            template=pipeline_config.get("template", "generic"),
            quality_threshold=pipeline_config.get("quality_threshold", 0.90),
            max_iterations=pipeline_config.get("max_iterations", 10),
            concurrent_loops=pipeline_config.get("concurrent_loops", 1),
        )

    def _build_pipeline_config_dict(
        self, objective: Objective
    ) -> Dict[str, Any]:
        """
        Build PipelineEngine config dict from Objective.

        Args:
            objective: The objective being executed

        Returns:
            Configuration dictionary for PipelineEngine.initialize()
        """
        base = objective.pipeline_config.copy() if objective.pipeline_config else {}
        # Inject orchestrator-specific metadata
        base["_orchestrator"] = True
        base["_objective_id"] = objective.objective_id
        return base

    def _extract_artifacts(
        self, snapshot: Any, objective: Objective
    ) -> list:
        """
        Extract artifacts from a PipelineSnapshot.

        Args:
            snapshot: PipelineSnapshot from PipelineEngine
            objective: The objective that was executed

        Returns:
            List of Artifact instances
        """
        artifacts = []
        if snapshot is None:
            return artifacts

        raw_artifacts = getattr(snapshot, "artifacts", {})
        if not raw_artifacts:
            return artifacts

        for key, value in raw_artifacts.items():
            artifacts.append(
                Artifact(
                    name=key,
                    artifact_type="pipeline_output",
                    url_or_path=str(value)[:256],
                    metadata={
                        "objective_id": objective.objective_id,
                        "source": "pipeline_engine",
                    },
                )
            )
        return artifacts

    async def execute(self, objective: Objective) -> ExecutionResult:
        """
        Execute an objective through PipelineEngine.

        Uses CircuitBreaker to protect against cascading failures.
        The correct invocation pattern:
            wrapped = self._circuit_breaker(self._do_execute)
            return await wrapped(obj)

        Args:
            objective: The objective to execute

        Returns:
            ExecutionResult with outcome details
        """
        try:
            if self._enable_circuit_breaker:
                # Correct CircuitBreaker usage:
                # 1. __call__ returns a wrapper function
                # 2. We then call the wrapper with arguments
                wrapped = self._circuit_breaker(self._do_execute)
                result = await wrapped(objective)
                return result
            else:
                return await self._do_execute(objective)

        except Exception as e:
            logger.error(
                f"Pipeline execution failed for objective "
                f"'{objective.title}' ({objective.objective_id}): {e}",
                extra={"objective_id": objective.objective_id},
            )
            return ExecutionResult(
                success=False,
                objective_id=objective.objective_id,
                error_message=str(e),
            )

    async def _do_execute(self, objective: Objective) -> ExecutionResult:
        """
        Internal execution logic (called through CircuitBreaker).

        Args:
            objective: The objective to execute

        Returns:
            ExecutionResult
        """
        engine = self.pipeline_engine

        # Build context and config from objective
        context = self._build_pipeline_context(objective)
        config = self._build_pipeline_config_dict(objective)

        # Initialize and run with guaranteed cleanup
        try:
            await engine.initialize(context, config)
            snapshot = await engine.start()
        finally:
            engine.shutdown()

        # Determine success
        final_state = getattr(snapshot, "state", None)
        success = final_state == PipelineState.COMPLETED

        # Extract quality score if available
        quality_score = getattr(snapshot, "quality_score", None)

        # Extract artifacts
        artifacts = self._extract_artifacts(snapshot, objective)

        error_msg = None
        if not success:
            error_msg = getattr(snapshot, "error_message", "Pipeline did not complete")

        return ExecutionResult(
            success=success,
            objective_id=objective.objective_id,
            pipeline_snapshot=snapshot,
            artifacts=artifacts,
            quality_score=quality_score,
            error_message=error_msg,
        )

    async def execute_with_result_update(
        self, objective: Objective
    ) -> ExecutionResult:
        """
        Execute objective and automatically update its status.

        Convenience method that handles the Objective lifecycle:
        - QUEUED -> IN_PROGRESS (before execution)
        - IN_PROGRESS -> COMPLETED (on success)
        - IN_PROGRESS -> BLOCKED (on failure)

        Args:
            objective: The objective to execute

        Returns:
            ExecutionResult with outcome details
        """
        # Transition to in_progress
        try:
            objective.transition_to(ObjectiveStatus.IN_PROGRESS)
        except ValueError as e:
            return ExecutionResult(
                success=False,
                objective_id=objective.objective_id,
                error_message=f"Cannot start: {e}",
            )

        # Execute
        result = await self.execute(objective)

        # Update objective based on result
        if result.success:
            objective.transition_to(ObjectiveStatus.COMPLETED)
            for artifact in result.artifacts:
                objective.add_artifact(artifact)
        else:
            objective.transition_to(ObjectiveStatus.BLOCKED)
            objective.error_message = result.error_message

        return result

    def get_circuit_breaker_stats(self) -> Dict[str, Any]:
        """Get circuit breaker statistics for monitoring."""
        return self._circuit_breaker.get_statistics()

    async def execute_without_status_update(
        self, objective: Objective
    ) -> dict:
        """
        Execute an objective through PipelineEngine WITHOUT mutating status.

        Nearly identical to _do_execute() but omits the objective
        transition_to() and add_artifact() calls. Returns a plain dict
        instead of ExecutionResult.

        Args:
            objective: The objective to execute

        Returns:
            dict with keys: success (bool), artifacts (list), error (str or None)
        """
        try:
            if self._enable_circuit_breaker:
                wrapped = self._circuit_breaker(self._do_execute_no_mutation)
                result = await wrapped(objective)
                return result
            else:
                return await self._do_execute_no_mutation(objective)

        except Exception as e:
            logger.error(
                f"Pipeline execution failed for objective "
                f"'{objective.title}' ({objective.objective_id}): {e}",
                extra={"objective_id": objective.objective_id},
            )
            return {
                "success": False,
                "artifacts": [],
                "error": str(e),
            }

    async def _do_execute_no_mutation(self, objective: Objective) -> dict:
        """
        Internal execution logic without objective status mutation.

        Args:
            objective: The objective to execute

        Returns:
            dict with success, artifacts, error keys
        """
        engine = self.pipeline_engine

        # Build context and config from objective
        context = self._build_pipeline_context(objective)
        config = self._build_pipeline_config_dict(objective)

        # Initialize and run with guaranteed cleanup
        try:
            await engine.initialize(context, config)
            snapshot = await engine.start()
        finally:
            engine.shutdown()

        # Determine success
        final_state = getattr(snapshot, "state", None)
        success = final_state == PipelineState.COMPLETED

        # Extract artifacts
        artifacts = self._extract_artifacts(snapshot, objective)

        error_msg = None
        if not success:
            error_msg = getattr(snapshot, "error_message", "Pipeline did not complete")

        return {
            "success": success,
            "artifacts": artifacts,
            "error": error_msg,
        }
