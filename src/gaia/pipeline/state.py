"""
GAIA Pipeline State Machine

This module defines the state machine for pipeline execution, including:
- PipelineState: Enumeration of possible pipeline states
- PipelineContext: Immutable context for pipeline execution
- PipelineSnapshot: Mutable state snapshot
- PipelineStateMachine: Thread-safe state transition manager

The state machine ensures valid transitions and maintains a complete
audit trail of all state changes.
"""

from enum import Enum, auto
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List, Set
import threading

from gaia.exceptions import InvalidStateTransition


class PipelineState(Enum):
    """
    Enumeration of pipeline states.

    States represent the lifecycle of a pipeline execution:
    - INITIALIZING: Pipeline is being configured
    - READY: Pipeline is configured and ready to start
    - RUNNING: Pipeline is actively executing
    - PAUSED: Pipeline is waiting for external input
    - COMPLETED: Pipeline finished successfully
    - FAILED: Pipeline encountered an error
    - CANCELLED: Pipeline was cancelled by user
    """

    INITIALIZING = auto()
    READY = auto()
    RUNNING = auto()
    PAUSED = auto()
    COMPLETED = auto()
    FAILED = auto()
    CANCELLED = auto()

    def is_terminal(self) -> bool:
        """Check if this is a terminal state (no outgoing transitions)."""
        return self in {
            PipelineState.COMPLETED,
            PipelineState.FAILED,
            PipelineState.CANCELLED,
        }

    def is_active(self) -> bool:
        """Check if pipeline is in an active state."""
        return self in {
            PipelineState.INITIALIZING,
            PipelineState.READY,
            PipelineState.RUNNING,
            PipelineState.PAUSED,
        }


@dataclass(frozen=True)
class PipelineContext:
    """
    Immutable context for a pipeline execution.

    The context contains all configuration and initial state that defines
    what the pipeline should accomplish. It is created at pipeline creation
    and remains unchanged throughout execution.

    Attributes:
        pipeline_id: Unique identifier for this pipeline
        user_goal: Natural language description of what user wants to achieve
        created_at: Timestamp when pipeline was created
        metadata: Additional context and configuration
        template: Quality template name (STANDARD, RAPID, ENTERPRISE, etc.)
        quality_threshold: Required quality score threshold (0-1)
        max_iterations: Maximum loop iterations before failure
        concurrent_loops: Number of concurrent loops to support
    """

    pipeline_id: str
    user_goal: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Dict[str, Any] = field(default_factory=dict)
    template: str = "STANDARD"
    quality_threshold: float = 0.90
    max_iterations: int = 10
    concurrent_loops: int = 5

    def __post_init__(self) -> None:
        """Validate context after initialization."""
        if not self.pipeline_id:
            raise ValueError("pipeline_id is required")
        if not self.user_goal:
            raise ValueError("user_goal is required")
        if not 0 <= self.quality_threshold <= 1:
            raise ValueError("quality_threshold must be between 0 and 1")
        if self.max_iterations < 0:
            raise ValueError("max_iterations must be non-negative")
        if self.concurrent_loops < 1:
            raise ValueError("concurrent_loops must be at least 1")

    def with_updates(self, **kwargs: Any) -> "PipelineContext":
        """
        Create a new context with updated values.

        Since PipelineContext is immutable (frozen), this creates a copy
        with the specified fields updated.

        Args:
            **kwargs: Fields to update

        Returns:
            New PipelineContext with updates applied
        """
        return PipelineContext(
            pipeline_id=self.pipeline_id,
            user_goal=self.user_goal,
            created_at=self.created_at,
            metadata={**self.metadata, **kwargs.get("metadata", {})},
            template=kwargs.get("template", self.template),
            quality_threshold=kwargs.get("quality_threshold", self.quality_threshold),
            max_iterations=kwargs.get("max_iterations", self.max_iterations),
            concurrent_loops=kwargs.get("concurrent_loops", self.concurrent_loops),
        )


@dataclass
class PipelineSnapshot:
    """
    Mutable snapshot of pipeline state at a point in time.

    The snapshot captures the current execution state, including:
    - Current state and phase
    - Loop information
    - Quality metrics
    - Artifacts produced
    - Chronicle (event log)
    - Timing information

    This class is modified by the PipelineStateMachine as the pipeline
    progresses through its lifecycle.
    """

    state: PipelineState
    current_phase: Optional[str] = None
    current_loop: Optional[int] = None
    iteration_count: int = 0
    quality_score: Optional[float] = None
    error_message: Optional[str] = None
    artifacts: Dict[str, Any] = field(default_factory=dict)
    chronicle: List[Dict[str, Any]] = field(default_factory=list)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    defects: List[Dict[str, Any]] = field(default_factory=list)
    context_injected: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert snapshot to dictionary for serialization.

        Returns:
            Dictionary representation of the snapshot
        """
        return {
            "state": self.state.name,
            "current_phase": self.current_phase,
            "current_loop": self.current_loop,
            "iteration_count": self.iteration_count,
            "quality_score": self.quality_score,
            "error_message": self.error_message,
            "artifacts": self.artifacts,
            "chronicle": self.chronicle,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "defects": self.defects,
            "context_injected": self.context_injected,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PipelineSnapshot":
        """
        Create snapshot from dictionary.

        Args:
            data: Dictionary with snapshot data

        Returns:
            PipelineSnapshot instance
        """
        return cls(
            state=PipelineState[data["state"]],
            current_phase=data.get("current_phase"),
            current_loop=data.get("current_loop"),
            iteration_count=data.get("iteration_count", 0),
            quality_score=data.get("quality_score"),
            error_message=data.get("error_message"),
            artifacts=data.get("artifacts", {}),
            chronicle=data.get("chronicle", []),
            started_at=(
                datetime.fromisoformat(data["started_at"]) if data.get("started_at") else None
            ),
            completed_at=(
                datetime.fromisoformat(data["completed_at"]) if data.get("completed_at") else None
            ),
            defects=data.get("defects", []),
            context_injected=data.get("context_injected", {}),
        )

    def elapsed_time(self) -> Optional[float]:
        """
        Calculate elapsed time since pipeline started.

        Returns:
            Elapsed time in seconds, or None if not started
        """
        if not self.started_at:
            return None

        end_time = self.completed_at or datetime.now(timezone.utc)
        return (end_time - self.started_at).total_seconds()


@dataclass
class StateTransition:
    """
    Record of a state transition.

    Captures details about when and why a state change occurred.

    Attributes:
        timestamp: When the transition occurred
        from_state: Previous state
        to_state: New state
        reason: Human-readable reason for transition
        metadata: Additional context about the transition
    """

    timestamp: datetime
    from_state: PipelineState
    to_state: PipelineState
    reason: str
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "timestamp": self.timestamp.isoformat(),
            "from_state": self.from_state.name,
            "to_state": self.to_state.name,
            "reason": self.reason,
            "metadata": self.metadata,
        }


class PipelineStateMachine:
    """
    Thread-safe state machine for pipeline execution.

    The PipelineStateMachine manages state transitions for a pipeline,
    ensuring that only valid transitions occur and maintaining a complete
    audit trail of all state changes.

    Valid Transitions:
        INITIALIZING -> READY (config.valid())
        INITIALIZING -> FAILED (error during init)
        READY -> RUNNING (start())
        READY -> CANCELLED (user.cancel())
        RUNNING -> PAUSED (wait())
        RUNNING -> COMPLETED (phase.complete() on final phase)
        RUNNING -> FAILED (error)
        PAUSED -> RUNNING (resume())
        PAUSED -> CANCELLED (cancel())

    Thread Safety:
        All state transitions are protected by a lock to ensure
        thread-safe operation in concurrent environments.

    Example:
        >>> context = PipelineContext(
        ...     pipeline_id="test-001",
        ...     user_goal="Build an API"
        ... )
        >>> fsm = PipelineStateMachine(context)
        >>> fsm.transition(PipelineState.READY, "Config validated")
        True
        >>> fsm.current_state
        <PipelineState.READY: 2>
    """

    # Define valid state transitions
    VALID_TRANSITIONS: Dict[PipelineState, Set[PipelineState]] = {
        PipelineState.INITIALIZING: {PipelineState.READY, PipelineState.FAILED},
        PipelineState.READY: {PipelineState.RUNNING, PipelineState.CANCELLED},
        PipelineState.RUNNING: {
            PipelineState.PAUSED,
            PipelineState.COMPLETED,
            PipelineState.FAILED,
        },
        PipelineState.PAUSED: {PipelineState.RUNNING, PipelineState.CANCELLED},
        PipelineState.COMPLETED: set(),  # Terminal state
        PipelineState.FAILED: set(),  # Terminal state
        PipelineState.CANCELLED: set(),  # Terminal state
    }

    def __init__(self, context: PipelineContext):
        """
        Initialize the state machine.

        Args:
            context: Pipeline context (immutable configuration)
        """
        self._context = context
        self._snapshot = PipelineSnapshot(state=PipelineState.INITIALIZING)
        self._transition_log: List[StateTransition] = []
        self._lock = threading.RLock()  # Reentrant lock for nested calls

    @property
    def context(self) -> PipelineContext:
        """Get the pipeline context (immutable)."""
        return self._context

    @property
    def snapshot(self) -> PipelineSnapshot:
        """Get a copy of the current state snapshot."""
        with self._lock:
            return self._snapshot

    @property
    def current_state(self) -> PipelineState:
        """Get the current pipeline state."""
        with self._lock:
            return self._snapshot.state

    @property
    def transition_log(self) -> List[StateTransition]:
        """Get the complete transition history."""
        with self._lock:
            return list(self._transition_log)

    @property
    def chronicle(self) -> List[Dict[str, Any]]:
        """Get the pipeline chronicle (event log)."""
        with self._lock:
            return list(self._snapshot.chronicle)

    def is_valid_transition(self, new_state: PipelineState) -> bool:
        """
        Check if a transition to the new state is valid.

        Args:
            new_state: Target state to check

        Returns:
            True if transition is valid, False otherwise
        """
        with self._lock:
            return new_state in self.VALID_TRANSITIONS.get(self._snapshot.state, set())

    def transition(
        self,
        new_state: PipelineState,
        reason: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        Attempt to transition to a new state.

        This is the primary method for changing pipeline state. It validates
        the transition, updates the snapshot, and logs the change.

        Args:
            new_state: Target state
            reason: Human-readable reason for the transition
            metadata: Optional additional context

        Returns:
            True if transition was successful

        Raises:
            InvalidStateTransition: If the transition is not valid

        Example:
            >>> fsm = PipelineStateMachine(context)
            >>> fsm.transition(PipelineState.READY, "Configuration loaded")
            True
        """
        with self._lock:
            old_state = self._snapshot.state

            # Validate transition
            if new_state not in self.VALID_TRANSITIONS.get(old_state, set()):
                raise InvalidStateTransition(
                    f"Cannot transition from {old_state.name} to {new_state.name}",
                    from_state=old_state.name,
                    to_state=new_state.name,
                )

            # Update state
            self._snapshot.state = new_state

            # Update timestamps based on state
            now = datetime.now(timezone.utc)
            self._update_timestamps(new_state, old_state, now)

            # Create transition record
            transition = StateTransition(
                timestamp=now,
                from_state=old_state,
                to_state=new_state,
                reason=reason,
                metadata=metadata or {},
            )
            self._transition_log.append(transition)

            # Add to chronicle
            self._snapshot.chronicle.append(
                {
                    "event": "STATE_TRANSITION",
                    "timestamp": now.isoformat(),
                    "from_state": old_state.name,
                    "to_state": new_state.name,
                    "reason": reason,
                }
            )

            return True

    def _update_timestamps(
        self,
        new_state: PipelineState,
        old_state: PipelineState,
        now: datetime,
    ) -> None:
        """Update started_at and completed_at timestamps."""
        if new_state == PipelineState.RUNNING and old_state == PipelineState.READY:
            self._snapshot.started_at = now
        elif new_state in {
            PipelineState.COMPLETED,
            PipelineState.FAILED,
            PipelineState.CANCELLED,
        }:
            self._snapshot.completed_at = now

    def set_phase(self, phase_name: str) -> None:
        """
        Set the current phase.

        Args:
            phase_name: Name of the current phase
        """
        with self._lock:
            self._snapshot.current_phase = phase_name

    def set_loop(self, loop_id: int) -> None:
        """
        Set the current loop.

        Args:
            loop_id: Current loop number
        """
        with self._lock:
            self._snapshot.current_loop = loop_id

    def increment_iteration(self) -> int:
        """
        Increment the iteration counter.

        Returns:
            New iteration count
        """
        with self._lock:
            self._snapshot.iteration_count += 1
            return self._snapshot.iteration_count

    def set_quality_score(self, score: float) -> None:
        """
        Set the current quality score.

        Args:
            score: Quality score (0-1)
        """
        with self._lock:
            self._snapshot.quality_score = score

    def set_error(self, error_message: str) -> None:
        """
        Set an error message (usually before FAILED state).

        Args:
            error_message: Description of the error
        """
        with self._lock:
            self._snapshot.error_message = error_message

    def add_artifact(self, name: str, artifact: Any) -> None:
        """
        Add an artifact to the snapshot.

        Args:
            name: Artifact name/key
            artifact: Artifact data
        """
        with self._lock:
            self._snapshot.artifacts[name] = artifact

    def add_defect(self, defect: Dict[str, Any]) -> None:
        """
        Add a defect to the snapshot.

        Args:
            defect: Defect information
        """
        with self._lock:
            self._snapshot.defects.append(defect)

    def add_defects(self, defects: List[Dict[str, Any]]) -> None:
        """
        Add multiple defects to the snapshot.

        Args:
            defects: List of defect information
        """
        with self._lock:
            self._snapshot.defects.extend(defects)

    def inject_context(self, context: Dict[str, Any]) -> None:
        """
        Inject additional context into the snapshot.

        Args:
            context: Context to inject
        """
        with self._lock:
            self._snapshot.context_injected.update(context)

    def add_chronicle_entry(
        self,
        event: str,
        data: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Add an entry to the chronicle.

        Args:
            event: Event name
            data: Event data
        """
        with self._lock:
            self._snapshot.chronicle.append(
                {
                    "event": event,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "pipeline_id": self._context.pipeline_id,
                    "phase": self._snapshot.current_phase,
                    "data": data or {},
                }
            )

    def get_state_info(self) -> Dict[str, Any]:
        """
        Get comprehensive state information.

        Returns:
            Dictionary with full state details
        """
        with self._lock:
            return {
                "state": self._snapshot.state.name,
                "phase": self._snapshot.current_phase,
                "loop": self._snapshot.current_loop,
                "iteration": self._snapshot.iteration_count,
                "quality_score": self._snapshot.quality_score,
                "started_at": (
                    self._snapshot.started_at.isoformat() if self._snapshot.started_at else None
                ),
                "completed_at": (
                    self._snapshot.completed_at.isoformat() if self._snapshot.completed_at else None
                ),
                "artifacts_count": len(self._snapshot.artifacts),
                "defects_count": len(self._snapshot.defects),
                "chronicle_entries": len(self._snapshot.chronicle),
            }

    def reset_to_ready(self) -> None:
        """
        Reset the state machine to READY state.

        Used for pipeline restart after configuration changes.
        """
        with self._lock:
            self._snapshot = PipelineSnapshot(state=PipelineState.READY)
            self._transition_log.clear()
            self._transition_log.append(
                StateTransition(
                    timestamp=datetime.now(timezone.utc),
                    from_state=PipelineState.INITIALIZING,
                    to_state=PipelineState.READY,
                    reason="Reset to ready",
                )
            )

    def is_terminal(self) -> bool:
        """
        Check if pipeline is in a terminal state.

        Returns:
            True if in COMPLETED, FAILED, or CANCELLED state
        """
        with self._lock:
            return self._snapshot.state.is_terminal()

    def is_active(self) -> bool:
        """
        Check if pipeline is in an active state.

        Returns:
            True if pipeline can still make progress
        """
        with self._lock:
            return self._snapshot.state.is_active()
