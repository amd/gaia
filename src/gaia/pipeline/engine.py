"""
GAIA Pipeline Engine

Main pipeline orchestrator that coordinates all components.
"""

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from gaia.agents.registry import AgentRegistry
from gaia.exceptions import (
    InvalidQualityThresholdError,
    PipelineAlreadyRunningError,
    PipelineNotInitializedError,
)
from gaia.hooks.base import HookContext
from gaia.hooks.production.context_hooks import (
    ContextInjectionHook,
    OutputProcessingHook,
)
from gaia.hooks.production.quality_hooks import (
    ChronicleHarvestHook,
    DefectExtractionHook,
    PipelineNotificationHook,
    QualityGateHook,
)
from gaia.hooks.production.validation_hooks import (
    PostActionValidationHook,
    PreActionValidationHook,
)
from gaia.hooks.registry import HookExecutor, HookRegistry
from gaia.pipeline.decision_engine import Decision, DecisionEngine, DecisionType
from gaia.pipeline.loop_manager import LoopConfig, LoopManager
from gaia.pipeline.metrics_collector import (
    PipelineMetricsCollector,
    get_pipeline_collector,
)
from gaia.pipeline.metrics_hooks import create_metrics_hook_group
from gaia.pipeline.recursive_template import get_recursive_template
from gaia.pipeline.routing_engine import RoutingEngine
from gaia.pipeline.state import (
    PipelineContext,
    PipelineSnapshot,
    PipelineState,
    PipelineStateMachine,
)
from gaia.quality.scorer import QualityScorer
from gaia.utils.id_generator import generate_loop_id
from gaia.utils.logging import get_logger, setup_logging

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

    template: str = "generic"
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
        max_concurrent_loops: int = 100,
        worker_pool_size: int = 4,
        model_id: Optional[str] = None,
        skip_lemonade: bool = False,
    ):
        """
        Initialize pipeline engine.

        Args:
            agents_dir: Directory for agent definitions
            enable_logging: Whether to setup logging
            log_level: Logging level
            max_concurrent_loops: Maximum number of concurrent pipeline loops (default: 100)
            worker_pool_size: Worker pool semaphore size for bounded execution (default: 4)
            model_id: Override model ID for all agents (default: None, uses agent YAML or template)
            skip_lemonade: If True, skip Lemonade server initialization in agents (stub/CI mode)
        """
        if enable_logging:
            setup_logging(level=log_level)

        self._agents_dir = agents_dir
        self._model_id = model_id
        self._skip_lemonade = skip_lemonade
        self._initialized = False
        self._running = False

        # Bounded concurrency configuration
        self.max_concurrent_loops = max_concurrent_loops
        self._semaphore = asyncio.Semaphore(max_concurrent_loops)
        self._worker_semaphore = asyncio.Semaphore(worker_pool_size)

        # Components (initialized in initialize())
        self._state_machine: Optional[PipelineStateMachine] = None
        self._loop_manager: Optional[LoopManager] = None
        self._decision_engine: Optional[DecisionEngine] = None
        self._quality_scorer: Optional[QualityScorer] = None
        self._agent_registry: Optional[AgentRegistry] = None
        self._hook_registry: Optional[HookRegistry] = None
        self._hook_executor: Optional[HookExecutor] = None
        self._routing_engine: Optional[RoutingEngine] = None

        # State
        self._context: Optional[PipelineContext] = None
        self._config: Optional[Dict[str, Any]] = None
        self._completion_event: Optional[asyncio.Event] = None

        # Template-driven phase configuration (not yet wired — see _get_phase_config)
        self._current_template = None

        # Metrics collector (initialized in initialize())
        self._metrics_collector: Optional[PipelineMetricsCollector] = None

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

        # Initialize metrics collector
        self._metrics_collector = get_pipeline_collector(
            pipeline_id=context.pipeline_id,
        )

        # Initialize decision engine
        self._decision_engine = DecisionEngine(self._config)

        # Initialize quality scorer
        self._quality_scorer = QualityScorer()

        # Resolve agents_dir — use config, then constructor arg, then default path
        agents_dir = self._config.get("agents_dir", self._agents_dir)
        if agents_dir is None:
            _default = Path(__file__).parent.parent.parent.parent / "config" / "agents"
            if _default.exists():
                agents_dir = str(_default)
        logger.info(f"AgentRegistry agents_dir: {agents_dir}")

        # Initialize agent registry BEFORE loop manager so it can be wired in
        self._agent_registry = AgentRegistry(agents_dir=agents_dir)
        await self._agent_registry.initialize()

        # Load template BEFORE constructing LoopManager so default_model is available
        template_name = (self._config.get("template") or "generic").lower()
        try:
            self._current_template = get_recursive_template(template_name)
            logger.info(
                f"Loaded pipeline template: {template_name}",
                extra={"template": template_name},
            )
        except KeyError:
            logger.warning(
                f"Template '{template_name}' not found in registry, using 'generic' fallback",
                extra={"template": template_name},
            )
            self._current_template = get_recursive_template("generic")

        # Initialize loop manager with agent registry and resolved model_id wired in
        concurrent_loops = self._config.get(
            "concurrent_loops", context.concurrent_loops
        )
        self._loop_manager = LoopManager(
            max_concurrent=concurrent_loops,
            agent_registry=self._agent_registry,
            model_id=self._model_id,
            template_model_id=getattr(self._current_template, "default_model", None),
            skip_lemonade=self._skip_lemonade,
        )

        # Initialize routing engine
        self._routing_engine = RoutingEngine(agent_registry=self._agent_registry)

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

        # Register metrics hooks if metrics collector is available
        if self._metrics_collector:
            from gaia.pipeline.metrics_hooks import create_metrics_hook_group

            metrics_hooks = create_metrics_hook_group(self._metrics_collector)
            for hook in metrics_hooks:
                self._hook_registry.register(hook)
            logger.info(f"Registered {len(metrics_hooks)} metrics hooks")

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

        phase_failed = False
        for phase in phases:
            if not self._running:
                break

            phase_complete = await self._execute_phase(phase)

            if not phase_complete:
                logger.warning(f"Phase {phase} did not complete successfully")
                phase_failed = True
                break

        # Determine terminal state: cancellation vs. failure vs. success
        if phase_failed:
            self._state_machine.transition(
                PipelineState.FAILED,
                "Pipeline phase failed",
            )
        else:
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

        # Use template-driven agent sequence when available; fall back to registry
        template_agents = self._get_agents_for_phase(PipelinePhase.PLANNING)
        if template_agents:
            agent_sequence = template_agents
        else:
            agent_id = self._agent_registry.select_agent(
                task_description=self._context.user_goal,
                current_phase=PipelinePhase.PLANNING,
                state=self._get_state_dict(),
            )
            if agent_id:
                logger.info(f"Selected planning agent: {agent_id}")
                self._state_machine.add_artifact("planning_agent", agent_id)
            agent_sequence = [agent_id] if agent_id else []

        # Create planning loop
        loop_config = LoopConfig(
            loop_id=generate_loop_id(self._context.pipeline_id),
            phase_name=PipelinePhase.PLANNING,
            agent_sequence=agent_sequence,
            exit_criteria={
                "quality_threshold": self._context.quality_threshold,
                "goal": self._context.user_goal,
            },
            quality_threshold=self._context.quality_threshold,
            max_iterations=self._context.max_iterations,
        )
        await self._loop_manager.create_loop(loop_config)
        future = await self._loop_manager.start_loop(loop_config.loop_id)

        # Wait for loop completion
        loop_state = None
        if future is not None:
            loop_state = await asyncio.wrap_future(future)
            logger.info(
                f"Planning loop completed: status={loop_state.status.name}",
                extra={
                    "loop_id": loop_config.loop_id,
                    "status": loop_state.status.name,
                },
            )
            # Propagate agent LLM outputs to state machine so they appear in
            # snapshot.artifacts and are available to QualityScorer
            for agent_id, artifact_text in loop_state.artifacts.items():
                if artifact_text is not None:
                    self._state_machine.add_artifact(f"plan_{agent_id}", artifact_text)
            self._state_machine.add_chronicle_entry(
                "PLANNING_ARTIFACTS_PROPAGATED",
                {
                    "agent_ids": list(loop_state.artifacts.keys()),
                    "artifact_count": len(loop_state.artifacts),
                    "loop_status": loop_state.status.name,
                },
            )

        self._state_machine.increment_iteration()
        return True

    async def _execute_development(self) -> bool:
        """Execute development phase."""
        logger.info("Executing DEVELOPMENT phase")

        # Use template-driven agent sequence when available; fall back to registry
        template_agents = self._get_agents_for_phase(PipelinePhase.DEVELOPMENT)
        if template_agents:
            agent_sequence = template_agents
        else:
            agent_id = self._agent_registry.select_agent(
                task_description=self._context.user_goal,
                current_phase=PipelinePhase.DEVELOPMENT,
                state=self._get_state_dict(),
                required_capabilities=["full-stack-development"],
            )
            if agent_id:
                logger.info(f"Selected development agent: {agent_id}")
            agent_sequence = [agent_id] if agent_id else []

        # Create development loop
        loop_config = LoopConfig(
            loop_id=generate_loop_id(self._context.pipeline_id),
            phase_name=PipelinePhase.DEVELOPMENT,
            agent_sequence=agent_sequence,
            exit_criteria={
                "quality_threshold": self._context.quality_threshold,
                "goal": self._context.user_goal,
            },
            quality_threshold=self._context.quality_threshold,
            max_iterations=self._context.max_iterations,
        )
        await self._loop_manager.create_loop(loop_config)
        future = await self._loop_manager.start_loop(loop_config.loop_id)

        # Wait for loop completion
        loop_state = None
        if future is not None:
            loop_state = await asyncio.wrap_future(future)
            logger.info(
                f"Development loop completed: status={loop_state.status.name}",
                extra={
                    "loop_id": loop_config.loop_id,
                    "status": loop_state.status.name,
                },
            )
            # Propagate agent LLM outputs to state machine so they appear in
            # snapshot.artifacts and are available to QualityScorer
            for agent_id, artifact_text in loop_state.artifacts.items():
                if artifact_text is not None:
                    self._state_machine.add_artifact(f"code_{agent_id}", artifact_text)
            self._state_machine.add_chronicle_entry(
                "DEVELOPMENT_ARTIFACTS_PROPAGATED",
                {
                    "agent_ids": list(loop_state.artifacts.keys()),
                    "artifact_count": len(loop_state.artifacts),
                    "loop_status": loop_state.status.name,
                },
            )

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
                "template": self._config.get("template", "generic"),
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

        # Route defects through RoutingEngine if available
        if self._routing_engine:
            defects = self._state_machine.snapshot.defects or []
            if defects:
                routing_decisions = []
                for defect in defects:
                    # Normalize defect to dict if needed
                    defect_dict = (
                        defect
                        if isinstance(defect, dict)
                        else {"description": str(defect)}
                    )
                    routing_decision = self._routing_engine.route_defect(defect_dict)
                    routing_decisions.append(routing_decision.to_dict())
                self._state_machine.add_artifact("routing_decisions", routing_decisions)
                logger.info(
                    f"Routed {len(routing_decisions)} defects via RoutingEngine",
                    extra={"defect_count": len(routing_decisions)},
                )

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

    async def execute(self, workload: Any) -> Any:
        """
        Execute a single workload through the pipeline.

        This is the single-workload execution primitive used by
        execute_with_backpressure(). Callers may pass any workload
        representation; the default implementation delegates to start()
        if the engine is already initialized, or returns the workload
        unchanged when used in test/mock contexts.

        Args:
            workload: The workload to execute (pipeline context, dict, or any object)

        Returns:
            Pipeline snapshot or workload result
        """
        if self._initialized and self._state_machine:
            return await self.start()
        return workload

    async def execute_with_backpressure(
        self,
        workloads: list,
        progress_callback: Optional[Callable] = None,
    ) -> list:
        """
        Execute multiple workloads with bounded concurrency.

        Uses dual semaphores to control concurrency: the outer semaphore
        limits total concurrent loops to max_concurrent_loops, and the
        inner worker semaphore limits parallel worker execution to
        worker_pool_size.

        Args:
            workloads: List of workload items to execute
            progress_callback: Optional callback invoked after each completed
                execution. Receives the result as its argument.

        Returns:
            List of results in the same order as workloads. Exceptions are
            returned as exception objects (not raised) due to return_exceptions=True.
        """

        async def bounded_execute(workload):
            async with self._semaphore:
                async with self._worker_semaphore:
                    result = await self.execute(workload)
                    if progress_callback:
                        progress_callback(result)
                    return result

        tasks = [bounded_execute(w) for w in workloads]
        return await asyncio.gather(*tasks, return_exceptions=True)

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

    def _get_phase_config(self, phase_name: str) -> Optional[Any]:
        """
        Get phase configuration from template.

        Args:
            phase_name: Name of phase to get config for

        Returns:
            PhaseConfig if template has this phase, None otherwise
        """
        if not self._current_template:
            return None
        return self._current_template.get_phase(phase_name)

    def _get_agents_for_phase(self, phase_name: str) -> List[str]:
        """
        Get list of agent IDs for a phase from template.

        Args:
            phase_name: Name of phase

        Returns:
            List of agent IDs configured for this phase
        """
        phase_config = self._get_phase_config(phase_name)
        if phase_config and phase_config.agents:
            return list(phase_config.agents)

        if self._current_template:
            for category, agents in self._current_template.agent_categories.items():
                if category.lower() == phase_name.lower():
                    return list(agents)

        return []

    def _get_output_artifact_name(self, phase_name: str) -> str:
        """
        Get output artifact name for a phase from template.

        Args:
            phase_name: Name of phase

        Returns:
            Artifact name for phase output
        """
        phase_config = self._get_phase_config(phase_name)
        if phase_config and phase_config.exit_criteria.get("artifact"):
            return phase_config.exit_criteria["artifact"]

        default_artifacts = {
            "planning": "technical_plan",
            "development": "implementation",
            "quality": "quality_report",
            "decision": "decision",
        }
        return default_artifacts.get(phase_name.lower(), f"{phase_name.lower()}_output")

    def shutdown(self) -> None:
        """Shutdown pipeline and cleanup resources."""
        logger.info("Shutting down PipelineEngine")

        if self._loop_manager:
            self._loop_manager.shutdown(wait=False)

        if self._agent_registry:
            self._agent_registry.shutdown()

        if self._quality_scorer:
            self._quality_scorer.shutdown()

        self._initialized = False
        self._running = False
