"""
GAIA Loop Manager

Manages concurrent loop execution with priority-based scheduling.
"""

import asyncio
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, auto
from pathlib import Path
from typing import Any, Dict, List, Optional

from gaia.agents.configurable import ConfigurableAgent
from gaia.agents.registry import AgentRegistry
from gaia.exceptions import (
    AgentNotFoundError,
    LoopCreationError,
    LoopNotFoundError,
)
from gaia.utils.logging import get_logger

logger = get_logger(__name__)


class LoopStatus(Enum):
    """
    Loop execution status.

    Statuses represent the lifecycle of a loop:
    - PENDING: Loop created but not started
    - RUNNING: Loop is actively executing
    - WAITING: Loop is waiting for external input
    - COMPLETED: Loop finished successfully
    - FAILED: Loop encountered an error
    - CANCELLED: Loop was cancelled
    """

    PENDING = auto()
    RUNNING = auto()
    WAITING = auto()
    COMPLETED = auto()
    FAILED = auto()
    CANCELLED = auto()

    def is_terminal(self) -> bool:
        """Check if this is a terminal status."""
        return self in {LoopStatus.COMPLETED, LoopStatus.FAILED, LoopStatus.CANCELLED}

    def is_active(self) -> bool:
        """Check if loop is in an active status."""
        return self in {LoopStatus.PENDING, LoopStatus.RUNNING, LoopStatus.WAITING}


@dataclass
class LoopConfig:
    """
    Configuration for a single execution loop.

    Attributes:
        loop_id: Unique loop identifier
        phase_name: Pipeline phase this loop belongs to
        agent_sequence: Ordered list of agent IDs to execute
        exit_criteria: Conditions for loop exit
        quality_threshold: Required quality score (0-1)
        max_iterations: Maximum iterations (0 = unlimited)
        timeout_seconds: Execution timeout
        priority: Loop priority for scheduling
    """

    loop_id: str
    phase_name: str
    agent_sequence: List[str]
    exit_criteria: Dict[str, Any]
    quality_threshold: float = 0.90
    max_iterations: int = 10
    timeout_seconds: int = 3600
    priority: int = 0

    def __post_init__(self):
        """Validate configuration."""
        if not self.loop_id:
            raise ValueError("loop_id is required")
        if not self.phase_name:
            raise ValueError("phase_name is required")
        if not 0 <= self.quality_threshold <= 1:
            raise ValueError("quality_threshold must be between 0 and 1")
        if self.max_iterations < 0:
            raise ValueError("max_iterations must be non-negative")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")


@dataclass
class LoopState:
    """
    Runtime state for a single loop.

    Attributes:
        config: Loop configuration
        status: Current loop status
        iteration: Current iteration number
        current_agent: Currently executing agent
        quality_scores: History of quality scores
        artifacts: Artifacts produced by the loop
        defects: Defects discovered
        error: Error message if failed
        started_at: When loop started
        completed_at: When loop completed
        result: Final loop result
    """

    config: LoopConfig
    status: LoopStatus = LoopStatus.PENDING
    iteration: int = 0
    current_agent: Optional[str] = None
    quality_scores: List[float] = field(default_factory=list)
    artifacts: Dict[str, Any] = field(default_factory=dict)
    defects: List[Dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    result: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "loop_id": self.config.loop_id,
            "phase_name": self.config.phase_name,
            "status": self.status.name,
            "iteration": self.iteration,
            "current_agent": self.current_agent,
            "quality_scores": self.quality_scores,
            "artifacts": self.artifacts,
            "defects_count": len(self.defects),
            "defects": self.defects,
            "error": self.error,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": (
                self.completed_at.isoformat() if self.completed_at else None
            ),
            "result": self.result,
        }

    @property
    def average_quality(self) -> Optional[float]:
        """Get average quality score."""
        if not self.quality_scores:
            return None
        return sum(self.quality_scores) / len(self.quality_scores)

    @property
    def max_quality(self) -> Optional[float]:
        """Get maximum quality score achieved."""
        if not self.quality_scores:
            return None
        return max(self.quality_scores)

    def quality_threshold_met(self) -> bool:
        """Check if quality threshold is met."""
        if not self.quality_scores:
            return False
        return self.quality_scores[-1] >= self.config.quality_threshold


class LoopManager:
    """
    Manages concurrent loop execution.

    The LoopManager handles:
    - Creating and registering loops
    - Scheduling with priority-based ordering
    - Concurrent execution (supports 5+ concurrent loops)
    - Resource pooling
    - Loop state tracking

    Example:
        >>> manager = LoopManager(max_concurrent=5)
        >>> config = LoopConfig(
        ...     loop_id="loop-001",
        ...     phase_name="DEVELOPMENT",
        ...     agent_sequence=["senior-developer", "quality-reviewer"],
        ...     exit_criteria={"quality_threshold": 0.9}
        ... )
        >>> await manager.create_loop(config)
        >>> await manager.start_loop("loop-001")
    """

    # Default maximum concurrent loops
    DEFAULT_MAX_CONCURRENT = 10

    def __init__(
        self,
        max_concurrent: int = DEFAULT_MAX_CONCURRENT,
        agent_registry: Optional[AgentRegistry] = None,
    ):
        """
        Initialize loop manager.

        Args:
            max_concurrent: Maximum concurrent loops (supports 5+)
            agent_registry: Optional agent registry for executing agents
        """
        self.MAX_CONCURRENT_LOOPS = max_concurrent
        self._agent_registry = agent_registry

        # Loop storage
        self._loops: Dict[str, LoopState] = {}

        # Execution
        self._executor = ThreadPoolExecutor(max_workers=max_concurrent)
        self._pending_queue: List[str] = []  # Loop IDs waiting to run
        self._running_futures: Dict[str, Future] = {}

        # State
        self._running_count = 0
        self._lock = asyncio.Lock()
        self._futures_lock = threading.Lock()

        logger.info(
            "LoopManager initialized",
            extra={"max_concurrent": max_concurrent},
        )

    async def create_loop(self, config: LoopConfig) -> str:
        """
        Create and register a new loop.

        Args:
            config: Loop configuration

        Returns:
            Loop ID

        Raises:
            LoopCreationError: If loop creation fails
        """
        async with self._lock:
            # Check for duplicate ID
            if config.loop_id in self._loops:
                raise LoopCreationError(
                    f"Loop already exists: {config.loop_id}",
                    config=(
                        config.to_dict() if hasattr(config, "to_dict") else str(config)
                    ),
                )

            # Create loop state
            loop_state = LoopState(config=config)
            self._loops[config.loop_id] = loop_state

        logger.info(
            f"Created loop: {config.loop_id}",
            extra={
                "loop_id": config.loop_id,
                "phase": config.phase_name,
            },
        )

        return config.loop_id

    async def start_loop(self, loop_id: str) -> Optional[Future]:
        """
        Start loop execution.

        If at capacity, loop is queued for later execution.

        Args:
            loop_id: ID of loop to start

        Returns:
            Future representing loop execution, or None if queued

        Raises:
            LoopNotFoundError: If loop not found
        """
        async with self._lock:
            if loop_id not in self._loops:
                raise LoopNotFoundError(loop_id)

            loop_state = self._loops[loop_id]

            # Check if already running
            if loop_state.status == LoopStatus.RUNNING:
                logger.warning(f"Loop {loop_id} is already running")
                return self._running_futures.get(loop_id)

            # Check capacity
            if self._running_count >= self.MAX_CONCURRENT_LOOPS:
                self._pending_queue.append(loop_id)
                logger.debug(
                    f"Loop {loop_id} queued (at capacity: {self._running_count}/{self.MAX_CONCURRENT_LOOPS})"
                )
                return None

            # Start loop
            self._running_count += 1
            loop_state.status = LoopStatus.RUNNING
            loop_state.started_at = datetime.now(timezone.utc)

            # Submit to executor
            future = self._executor.submit(self._execute_loop, loop_id)

            with self._futures_lock:
                self._running_futures[loop_id] = future

        logger.info(
            f"Started loop: {loop_id}",
            extra={"loop_id": loop_id},
        )

        return future

    def _execute_loop(self, loop_id: str) -> LoopState:
        """
        Execute a single loop through all iterations.

        This runs in a thread pool executor.

        Loop continues until:
        - Quality threshold met
        - Max iterations reached
        - Error occurs
        - Cancelled

        Args:
            loop_id: ID of loop to execute

        Returns:
            Final loop state
        """
        loop_state = self._loops[loop_id]

        try:
            while loop_state.status == LoopStatus.RUNNING:
                loop_state.iteration += 1

                logger.debug(
                    f"Loop {loop_id} iteration {loop_state.iteration}",
                    extra={"loop_id": loop_id, "iteration": loop_state.iteration},
                )

                # Execute agent sequence
                for agent_id in loop_state.config.agent_sequence:
                    if loop_state.status != LoopStatus.RUNNING:
                        break

                    loop_state.current_agent = agent_id

                    # Execute agent (would call AgentRegistry in production)
                    # For now, simulate with a result
                    result = self._execute_agent(agent_id, loop_state)

                    if result.get("success"):
                        loop_state.artifacts[agent_id] = result.get("artifact")
                    else:
                        loop_state.defects.append(
                            {
                                "agent": agent_id,
                                "error": result.get("error", "Unknown error"),
                                "iteration": loop_state.iteration,
                            }
                        )

                # Quality evaluation
                quality_score = self._evaluate_quality(loop_state)
                loop_state.quality_scores.append(quality_score)

                logger.debug(
                    f"Loop {loop_id} quality score: {quality_score:.2f}",
                    extra={"loop_id": loop_id, "quality_score": quality_score},
                )

                # Check exit criteria
                if quality_score >= loop_state.config.quality_threshold:
                    loop_state.status = LoopStatus.COMPLETED
                    logger.info(
                        f"Loop {loop_id} completed: quality {quality_score:.2f} >= threshold {loop_state.config.quality_threshold:.2f}",
                        extra={"loop_id": loop_id},
                    )
                    break

                # Check max iterations
                if (
                    loop_state.config.max_iterations > 0
                    and loop_state.iteration >= loop_state.config.max_iterations
                ):
                    loop_state.status = LoopStatus.FAILED
                    loop_state.error = (
                        f"Max iterations ({loop_state.config.max_iterations}) reached "
                        f"with quality {quality_score:.2f} < threshold {loop_state.config.quality_threshold:.2f}"
                    )
                    logger.warning(
                        f"Loop {loop_id} failed: max iterations exceeded",
                        extra={
                            "loop_id": loop_id,
                            "max_iterations": loop_state.config.max_iterations,
                        },
                    )
                    break

                # Continue to next iteration with defects
                # In production, would extract defects and pass to next iteration

            # Loop complete
            loop_state.completed_at = datetime.now(timezone.utc)
            loop_state.result = {
                "success": loop_state.status == LoopStatus.COMPLETED,
                "iterations": loop_state.iteration,
                "final_quality": quality_score if loop_state.quality_scores else None,
            }

        except Exception as e:
            loop_state.status = LoopStatus.FAILED
            loop_state.error = str(e)
            loop_state.completed_at = datetime.now(timezone.utc)
            logger.exception(f"Loop {loop_id} execution error: {e}")

        finally:
            # Cleanup
            self._on_loop_complete(loop_id)

        return loop_state

    def _execute_agent(
        self,
        agent_id: str,
        loop_state: LoopState,
    ) -> Dict[str, Any]:
        """
        Execute a single agent.

        Loads agent definition from registry, instantiates ConfigurableAgent,
        injects tools from YAML, and executes with proper context.

        Args:
            agent_id: Agent to execute
            loop_state: Current loop state

        Returns:
            Agent execution result

        Raises:
            AgentNotFoundError: If agent not found in registry
        """
        if not self._agent_registry:
            logger.warning("No agent registry configured - using stub execution")
            return {
                "success": True,
                "artifact": f"Stub artifact from {agent_id}",
            }

        # Get agent definition from registry
        agent_def = self._agent_registry.get_agent(agent_id)
        if not agent_def:
            logger.error(f"Agent not found in registry: {agent_id}")
            return {
                "success": False,
                "error": f"Agent not found: {agent_id}",
            }

        logger.info(
            f"Executing agent: {agent_id}",
            extra={
                "agent_id": agent_id,
                "tools": agent_def.tools,
                "capabilities": (
                    agent_def.capabilities.capabilities
                    if agent_def.capabilities
                    else []
                ),
            },
        )

        try:
            # Create configurable agent
            agent = ConfigurableAgent(
                definition=agent_def,
                tools_dir=Path("gaia/tools"),
                prompts_dir=Path("gaia/prompts"),
                silent_mode=True,  # Suppress console output in pipeline
            )

            # Initialize agent (registers tools, builds prompt)
            # Note: This is synchronous for now, could be async in future
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(agent.initialize())
            finally:
                loop.close()

            # Prepare execution context
            context = {
                "goal": loop_state.config.exit_criteria.get(
                    "goal", "Complete the task"
                ),
                "phase": loop_state.config.phase_name,
                "iteration": loop_state.iteration,
                "defects": loop_state.defects,
                "artifacts": loop_state.artifacts,
            }

            # Execute agent
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                result = loop.run_until_complete(agent.execute(context))
            finally:
                loop.close()

            logger.info(
                f"Agent {agent_id} execution complete",
                extra={
                    "agent_id": agent_id,
                    "success": result.get("success", False),
                },
            )

            return result

        except Exception as e:
            logger.exception(f"Agent {agent_id} execution failed: {e}")
            return {
                "success": False,
                "error": str(e),
                "agent_id": agent_id,
            }

    def _evaluate_quality(self, loop_state: LoopState) -> float:
        """
        Evaluate quality of loop output.

        In production, this would call the QualityScorer.

        Args:
            loop_state: Current loop state

        Returns:
            Quality score (0-1)
        """
        # Simulate quality evaluation
        # Base score starts at 0.7
        base_score = 0.7

        # Add some variation based on iteration
        iteration_bonus = min(0.03 * loop_state.iteration, 0.25)

        # Reduce for defects
        defect_penalty = 0.05 * len(loop_state.defects)

        score = base_score + iteration_bonus - defect_penalty
        return max(0.0, min(1.0, score))

    def _on_loop_complete(self, loop_id: str) -> None:
        """
        Handle loop completion and start next pending.

        Args:
            loop_id: ID of completed loop
        """

        async def _release():
            async with self._lock:
                self._running_count -= 1

                # Remove from running futures
                with self._futures_lock:
                    self._running_futures.pop(loop_id, None)

                # Start next pending loop
                if self._pending_queue:
                    next_loop_id = self._pending_queue.pop(0)
                    if next_loop_id in self._loops:
                        self._loops[next_loop_id].status = LoopStatus.RUNNING
                        self._loops[next_loop_id].started_at = datetime.now(
                            timezone.utc
                        )
                        self._running_count += 1

                        future = self._executor.submit(self._execute_loop, next_loop_id)
                        with self._futures_lock:
                            self._running_futures[next_loop_id] = future

                        logger.info(
                            f"Started queued loop: {next_loop_id}",
                            extra={"loop_id": next_loop_id},
                        )

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_release())
        except RuntimeError:
            # No event loop - create one
            asyncio.run(_release())

    def get_loop_state(self, loop_id: str) -> Optional[LoopState]:
        """
        Get current state of a loop.

        Args:
            loop_id: Loop ID

        Returns:
            LoopState or None
        """
        return self._loops.get(loop_id)

    def get_all_loops(self) -> Dict[str, LoopState]:
        """Get all loop states."""
        return dict(self._loops)

    def get_running_count(self) -> int:
        """Get number of currently running loops."""
        return self._running_count

    def get_pending_count(self) -> int:
        """Get number of pending loops in queue."""
        return len(self._pending_queue)

    async def cancel_loop(self, loop_id: str) -> bool:
        """
        Cancel a running loop.

        Args:
            loop_id: Loop ID to cancel

        Returns:
            True if cancelled, False if not found or already terminal
        """
        async with self._lock:
            if loop_id not in self._loops:
                return False

            loop_state = self._loops[loop_id]

            if loop_state.status.is_terminal():
                return False

            loop_state.status = LoopStatus.CANCELLED
            loop_state.completed_at = datetime.now(timezone.utc)

            # Cancel future if running
            with self._futures_lock:
                future = self._running_futures.get(loop_id)
                if future and not future.done():
                    future.cancel()

        logger.info(f"Cancelled loop: {loop_id}", extra={"loop_id": loop_id})
        return True

    def get_statistics(self) -> Dict[str, Any]:
        """Get loop manager statistics."""
        loops_by_status = {}
        for loop in self._loops.values():
            status = loop.status.name
            loops_by_status[status] = loops_by_status.get(status, 0) + 1

        return {
            "total_loops": len(self._loops),
            "running": self._running_count,
            "pending": len(self._pending_queue),
            "max_concurrent": self.MAX_CONCURRENT_LOOPS,
            "by_status": loops_by_status,
        }

    def shutdown(self, wait: bool = True) -> None:
        """
        Shutdown loop manager.

        Args:
            wait: Whether to wait for running loops to complete
        """
        logger.info("Shutting down LoopManager")
        self._executor.shutdown(wait=wait)
