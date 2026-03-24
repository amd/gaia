"""
GAIA Pipeline Engine

Main pipeline orchestrator that coordinates all components.
"""

import asyncio
from dataclasses import dataclass
from typing import Dict, List, Any, Optional

from gaia.pipeline.state import (
    PipelineState,
    PipelineContext,
    PipelineSnapshot,
    PipelineStateMachine,
)
from gaia.pipeline.loop_manager import LoopManager, LoopConfig
from gaia.pipeline.decision_engine import DecisionEngine, DecisionType
from gaia.quality.scorer import QualityScorer
from gaia.agents.registry import AgentRegistry
from gaia.hooks.base import HookContext
from gaia.hooks.registry import HookRegistry, HookExecutor
from gaia.hooks.production.validation_hooks import (
    PreActionValidationHook,
    PostActionValidationHook,
)
from gaia.hooks.production.context_hooks import (
    ContextInjectionHook,
    OutputProcessingHook,
)
from gaia.hooks.production.quality_hooks import (
    QualityGateHook,
    DefectExtractionHook,
    PipelineNotificationHook,
    ChronicleHarvestHook,
)
from gaia.utils.logging import get_logger, setup_logging
from gaia.utils.id_generator import generate_loop_id
from gaia.exceptions import (
    PipelineNotInitializedError,
    PipelineAlreadyRunningError,
    InvalidQualityThresholdError,
)


logger = get_logger(__name__)


# Pipeline phases
class PipelinePhase:
    """Pipeline phase constants."""

    PLANNING = "PLANNING"
    DEVELOPMENT = "DEVELOPMENT"
    QUALITY = "QUALITY"
    DECISION = "DECISION"

    ALL = [PLANNING, DEVELOPMENT, QUALITY, DECISION]


@dataclass
class PipelineConfig:
    """
    Pipeline configuration.

    Attributes:
        template: Quality template name
        quality_threshold: Required quality score (0-1)
        max_iterations: Maximum loop iterations
        concurrent_loops: Number of concurrent loops
        agents_dir: Directory for agent definitions
        enable_hooks: Whether to enable hooks
        hooks: List of hooks to register
    """

    template: str = "STANDARD"
    quality_threshold: float = 0.90
    max_iterations: int = 10
    concurrent_loops: int = 5
    agents_dir: Optional[str] = None
    enable_hooks: bool = True
    hooks: List[str] = None

    def __post_init__(self):
        if not 0 <= self.quality_threshold <= 1:
            raise InvalidQualityThresholdError(self.quality_threshold)
        if self.max_iterations < 0:
            raise ValueError("max_iterations must be non-negative")
        if self.concurrent_loops < 1:
            raise ValueError("concurrent_loops must be at least 1")


class PipelineEngine:
    """
    Main pipeline orchestrator.

    The PipelineEngine coordinates all pipeline components:
    - State machine for lifecycle management
    - Loop manager for concurrent execution
    - Decision engine for progression logic
    - Quality scorer for evaluation
    - Agent registry for agent selection
    - Hook executor for event handling

    Example:
        >>> engine = PipelineEngine()
        >>> context = PipelineContext(
        ...     pipeline_id="test-001",
        ...     user_goal="Build a REST API"
        ... )
        >>> await engine.initialize(context, {"template": "STANDARD"})
        >>> result = await engine.start()
        >>> print(f"Pipeline completed with state: {result.state}")
    """

    def __init__(
        self,
        agents_dir: Optional[str] = None,
        enable_logging: bool = True,
        log_level: int = 20,  # INFO
    ):
        """
        Initialize pipeline engine.

        Args:
            agents_dir: Directory for agent definitions
            enable_logging: Whether to setup logging
            log_level: Logging level
        """
        if enable_logging:
            setup_logging(level=log_level)

        self._agents_dir = agents_dir
        self._initialized = False
        self._running = False

        # Components (initialized in initialize())
        self._state_machine: Optional[PipelineStateMachine] = None
        self._loop_manager: Optional[LoopManager] = None
        self._decision_engine: Optional[DecisionEngine] = None
        self._quality_scorer: Optional[QualityScorer] = None
        self._agent_registry: Optional[AgentRegistry] = None
        self._hook_registry: Optional[HookRegistry] = None
        self._hook_executor: Optional[HookExecutor] = None

        # State
        self._context: Optional[PipelineContext] = None
        self._config: Optional[Dict[str, Any]] = None
        self._completion_event: Optional[asyncio.Event] = None

        logger.info("PipelineEngine created")

    async def initialize(
        self,
        context: PipelineContext,
        config: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Initialize pipeline with context and configuration.

        Args:
            context: Pipeline context
            config: Configuration dictionary

        Raises:
            PipelineAlreadyRunningError: If pipeline is already initialized
        """
        if self._initialized:
            raise PipelineAlreadyRunningError("Pipeline already initialized")

        logger.info(
            f"Initializing pipeline {context.pipeline_id}",
            extra={"pipeline_id": context.pipeline_id},
        )

        self._context = context
        self._config = config or {}

        # Initialize state machine
        self._state_machine = PipelineStateMachine(context)

        # Initialize loop manager
        concurrent_loops = self._config.get("concurrent_loops", context.concurrent_loops)
        self._loop_manager = LoopManager(
            max_concurrent=concurrent_loops,
            agent_registry=self._agent_registry,
        )

        # Initialize decision engine
        self._decision_engine = DecisionEngine(self._config)

        # Initialize quality scorer
        self._quality_scorer = QualityScorer()

        # Initialize agent registry
        agents_dir = self._config.get("agents_dir", self._agents_dir)
        self._agent_registry = AgentRegistry(agents_dir=agents_dir)
        await self._agent_registry.initialize()

        # Initialize hook system
        if self._config.get("enable_hooks", True):
            self._hook_registry = HookRegistry()
            self._hook_executor = HookExecutor(self._hook_registry)
            self._register_default_hooks()

        # Transition to READY state
        self._state_machine.transition(
            PipelineState.READY,
            "Initialization complete",
        )

        self._initialized = True
        self._completion_event = asyncio.Event()

        logger.info(
            f"Pipeline {context.pipeline_id} initialized",
            extra={"pipeline_id": context.pipeline_id},
        )

    def _register_default_hooks(self) -> None:
        """Register default production hooks."""
        if not self._hook_registry:
            return

        hooks = [
            PreActionValidationHook(),
            PostActionValidationHook(),
            ContextInjectionHook(),
            OutputProcessingHook(),
            QualityGateHook(),
            DefectExtractionHook(),
            PipelineNotificationHook(),
            ChronicleHarvestHook(),
        ]

        for hook in hooks:
            self._hook_registry.register(hook)

        logger.info(f"Registered {len(hooks)} default hooks")

    async def start(self) -> PipelineSnapshot:
        """
        Start pipeline execution.

        Returns:
            Current pipeline snapshot

        Raises:
            PipelineNotInitializedError: If not initialized
            PipelineAlreadyRunningError: If already running
        """
        if not self._initialized:
            raise PipelineNotInitializedError()

        if self._running:
            raise PipelineAlreadyRunningError()

        logger.info(
            f"Starting pipeline {self._context.pipeline_id}",
            extra={"pipeline_id": self._context.pipeline_id},
        )

        self._running = True

        # Transition to RUNNING
        self._state_machine.transition(PipelineState.RUNNING, "Pipeline started")

        # Execute pipeline phases
        try:
            await self._execute_pipeline()
        except Exception as e:
            logger.exception(f"Pipeline error: {e}")
            self._state_machine.transition(
                PipelineState.FAILED,
                f"Pipeline error: {e}",
            )
            self._running = False
            self._completion_event.set()

        return self._state_machine.snapshot

    async def _execute_pipeline(self) -> None:
        """Execute all pipeline phases."""
        phases = [
            PipelinePhase.PLANNING,
            PipelinePhase.DEVELOPMENT,
            PipelinePhase.QUALITY,
            PipelinePhase.DECISION,
        ]

        for phase in phases:
            if not self._running:
                break

            phase_complete = await self._execute_phase(phase)

            if not phase_complete:
                logger.warning(f"Phase {phase} did not complete successfully")
                break

        # Pipeline complete
        self._state_machine.transition(
            PipelineState.COMPLETED,
            "Pipeline execution complete",
        )
        self._running = False
        self._completion_event.set()

    async def _execute_phase(self, phase_name: str) -> bool:
        """
        Execute a single phase.

        Args:
            phase_name: Phase to execute

        Returns:
            True if phase completed successfully
        """
        logger.info(f"Executing phase: {phase_name}")

        self._state_machine.set_phase(phase_name)

        # Execute phase enter hooks
        if self._hook_executor:
            context = HookContext(
                event="PHASE_ENTER",
                pipeline_id=self._context.pipeline_id,
                phase=phase_name,
                state=self._get_state_dict(),
            )
            result = await self._hook_executor.execute_hooks("PHASE_ENTER", context)
            if result.halt_pipeline:
                return False

        # Execute phase based on type
        success = True
        if phase_name == PipelinePhase.PLANNING:
            success = await self._execute_planning()
        elif phase_name == PipelinePhase.DEVELOPMENT:
            success = await self._execute_development()
        elif phase_name == PipelinePhase.QUALITY:
            success = await self._execute_quality()
        elif phase_name == PipelinePhase.DECISION:
            success = await self._execute_decision()

        # Execute phase exit hooks
        if self._hook_executor:
            context = HookContext(
                event="PHASE_EXIT",
                pipeline_id=self._context.pipeline_id,
                phase=phase_name,
                state=self._get_state_dict(),
                data={"success": success},
            )
            result = await self._hook_executor.execute_hooks("PHASE_EXIT", context)
            if result.halt_pipeline:
                return False

        return success

    async def _execute_planning(self) -> bool:
        """Execute planning phase."""
        logger.info("Executing PLANNING phase")

        # Select planning agent
        agent_id = self._agent_registry.select_agent(
            task_description=self._context.user_goal,
            current_phase=PipelinePhase.PLANNING,
            state=self._get_state_dict(),
        )

        if agent_id:
            logger.info(f"Selected planning agent: {agent_id}")
            self._state_machine.add_artifact("planning_agent", agent_id)

        # Create planning loop
        loop_config = LoopConfig(
            loop_id=generate_loop_id(self._context.pipeline_id),
            phase_name=PipelinePhase.PLANNING,
            agent_sequence=[agent_id] if agent_id else [],
            exit_criteria={"quality_threshold": self._context.quality_threshold},
            quality_threshold=self._context.quality_threshold,
            max_iterations=self._context.max_iterations,
        )
        await self._loop_manager.create_loop(loop_config)
        await self._loop_manager.start_loop(loop_config.loop_id)

        # Wait for loop completion
        await asyncio.sleep(0.1)  # In production, would wait properly

        self._state_machine.increment_iteration()
        return True

    async def _execute_development(self) -> bool:
        """Execute development phase."""
        logger.info("Executing DEVELOPMENT phase")

        # Select development agent
        agent_id = self._agent_registry.select_agent(
            task_description=self._context.user_goal,
            current_phase=PipelinePhase.DEVELOPMENT,
            state=self._get_state_dict(),
            required_capabilities=["full-stack-development"],
        )

        if agent_id:
            logger.info(f"Selected development agent: {agent_id}")

        # Create development loop
        loop_config = LoopConfig(
            loop_id=generate_loop_id(self._context.pipeline_id),
            phase_name=PipelinePhase.DEVELOPMENT,
            agent_sequence=[agent_id] if agent_id else [],
            exit_criteria={"quality_threshold": self._context.quality_threshold},
            quality_threshold=self._context.quality_threshold,
            max_iterations=self._context.max_iterations,
        )
        await self._loop_manager.create_loop(loop_config)
        await self._loop_manager.start_loop(loop_config.loop_id)

        await asyncio.sleep(0.1)

        self._state_machine.increment_iteration()
        return True

    async def _execute_quality(self) -> bool:
        """Execute quality phase."""
        logger.info("Executing QUALITY phase")

        # Get artifacts to evaluate
        artifacts = self._state_machine.snapshot.artifacts

        # Evaluate quality
        quality_report = await self._quality_scorer.evaluate(
            artifact=artifacts,
            context={
                "requirements": [self._context.user_goal],
                "template": self._config.get("template", "STANDARD"),
            },
        )

        # Store quality score
        quality_score = quality_report.overall_score / 100
        self._state_machine.set_quality_score(quality_score)
        self._state_machine.add_artifact("quality_report", quality_report.to_dict())

        logger.info(
            f"Quality evaluation complete: {quality_score:.2f}",
            extra={"quality_score": quality_score},
        )

        return True

    async def _execute_decision(self) -> bool:
        """Execute decision phase."""
        logger.info("Executing DECISION phase")

        quality_score = self._state_machine.snapshot.quality_score or 0.0
        iteration = self._state_machine.snapshot.iteration_count

        # Make decision
        decision = self._decision_engine.evaluate(
            phase_name=PipelinePhase.DECISION,
            quality_score=quality_score,
            quality_threshold=self._context.quality_threshold,
            defects=self._state_machine.snapshot.defects,
            iteration=iteration,
            max_iterations=self._context.max_iterations,
            is_final_phase=True,
        )

        self._state_machine.add_artifact("decision", decision.to_dict())

        logger.info(
            f"Decision: {decision.decision_type.name}",
            extra={"decision_type": decision.decision_type.name},
        )

        # Handle decision
        if decision.decision_type == DecisionType.FAIL:
            self._state_machine.set_error(decision.reason)
            return False

        return True

    def _get_state_dict(self) -> Dict[str, Any]:
        """Get current state as dictionary."""
        snapshot = self._state_machine.snapshot
        return {
            "pipeline_id": self._context.pipeline_id,
            "user_goal": self._context.user_goal,
            "current_phase": snapshot.current_phase,
            "quality_score": snapshot.quality_score,
            "iteration_count": snapshot.iteration_count,
            "defects": snapshot.defects,
            "artifacts": snapshot.artifacts,
            "max_iterations": self._context.max_iterations,
        }

    async def pause(self, reason: str) -> PipelineSnapshot:
        """Pause pipeline execution."""
        if not self._initialized:
            raise PipelineNotInitializedError()

        self._state_machine.transition(PipelineState.PAUSED, reason)
        logger.info(f"Pipeline paused: {reason}")
        return self._state_machine.snapshot

    async def resume(self) -> PipelineSnapshot:
        """Resume paused pipeline."""
        if not self._initialized:
            raise PipelineNotInitializedError()

        if self._state_machine.current_state != PipelineState.PAUSED:
            raise PipelineNotInitializedError("Pipeline is not paused")

        self._state_machine.transition(PipelineState.RUNNING, "Pipeline resumed")
        self._running = True
        logger.info("Pipeline resumed")
        return self._state_machine.snapshot

    async def cancel(self) -> PipelineSnapshot:
        """Cancel pipeline execution."""
        if not self._initialized:
            raise PipelineNotInitializedError()

        self._running = False
        self._state_machine.transition(PipelineState.CANCELLED, "Pipeline cancelled")

        # Cancel all loops
        for loop_id in list(self._loop_manager.get_all_loops().keys()):
            await self._loop_manager.cancel_loop(loop_id)

        self._completion_event.set()
        logger.info("Pipeline cancelled")
        return self._state_machine.snapshot

    async def wait_for_completion(self, timeout: Optional[float] = None) -> bool:
        """
        Wait for pipeline to complete.

        Args:
            timeout: Maximum wait time in seconds

        Returns:
            True if completed, False if timeout
        """
        if not self._completion_event:
            return False

        try:
            await asyncio.wait_for(
                self._completion_event.wait(),
                timeout=timeout,
            )
            return True
        except asyncio.TimeoutError:
            return False

    def get_snapshot(self) -> PipelineSnapshot:
        """Get current pipeline state snapshot."""
        if not self._initialized:
            raise PipelineNotInitializedError()
        return self._state_machine.snapshot

    def get_chronicle(self) -> List[Dict[str, Any]]:
        """Get pipeline chronicle (event log)."""
        if not self._initialized:
            raise PipelineNotInitializedError()
        return self._state_machine.chronicle

    def get_loop_manager(self) -> LoopManager:
        """Get loop manager instance."""
        if not self._loop_manager:
            raise PipelineNotInitializedError()
        return self._loop_manager

    def shutdown(self) -> None:
        """Shutdown pipeline and cleanup resources."""
        logger.info("Shutting down PipelineEngine")

        if self._loop_manager:
            self._loop_manager.shutdown(wait=False)

        if self._agent_registry:
            self._agent_registry.shutdown()

        self._initialized = False
        self._running = False
