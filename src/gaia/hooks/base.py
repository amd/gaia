"""
GAIA Base Hook

Base class and context/result types for GAIA hooks.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, auto
from typing import Any, Dict, List, Optional


class HookPriority(Enum):
    """
    Hook execution priority.

    Priorities determine execution order when multiple hooks
    are registered for the same event.
    """

    HIGH = 1  # Execute first (critical hooks)
    NORMAL = 2  # Execute second (standard hooks)
    LOW = 3  # Execute last (logging/notification hooks)


class HookEvent(Enum):
    """
    Pipeline events that can trigger hooks.
    """

    # Lifecycle events
    PIPELINE_START = auto()
    PIPELINE_COMPLETE = auto()
    PIPELINE_FAILED = auto()
    PIPELINE_CANCELLED = auto()

    # Phase events
    PHASE_ENTER = auto()
    PHASE_EXIT = auto()

    # Loop events
    LOOP_START = auto()
    LOOP_END = auto()

    # Agent events
    AGENT_SELECT = auto()
    AGENT_EXECUTE = auto()
    AGENT_COMPLETE = auto()

    # Quality events
    QUALITY_EVAL = auto()
    QUALITY_RESULT = auto()

    # Decision events
    DECISION_MAKE = auto()

    # Processing events
    DEFECT_EXTRACT = auto()
    CONTEXT_INJECT = auto()
    OUTPUT_PROCESS = auto()


@dataclass
class HookContext:
    """
    Context passed to hooks during execution.

    Contains all relevant information about the current pipeline
    state and the event that triggered the hook.

    Attributes:
        event: Event name that triggered this hook
        pipeline_id: Unique pipeline identifier
        phase: Current pipeline phase (if applicable)
        loop_id: Current loop identifier (if applicable)
        agent_id: Current agent identifier (if applicable)
        state: Current pipeline state dictionary
        data: Event-specific data
        metadata: Additional context metadata
        correlation_id: ID for tracing across hooks
    """

    event: str
    pipeline_id: str
    phase: Optional[str] = None
    loop_id: Optional[str] = None
    agent_id: Optional[str] = None
    state: Dict[str, Any] = field(default_factory=dict)
    data: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    correlation_id: Optional[str] = None

    def __post_init__(self):
        """Set defaults after initialization."""
        if not self.correlation_id:
            self.correlation_id = (
                f"hook-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"
            )

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "event": self.event,
            "pipeline_id": self.pipeline_id,
            "phase": self.phase,
            "loop_id": self.loop_id,
            "agent_id": self.agent_id,
            "state": self.state,
            "data": self.data,
            "metadata": self.metadata,
            "correlation_id": self.correlation_id,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "HookContext":
        """Create from dictionary."""
        return cls(
            event=data.get("event", ""),
            pipeline_id=data.get("pipeline_id", ""),
            phase=data.get("phase"),
            loop_id=data.get("loop_id"),
            agent_id=data.get("agent_id"),
            state=data.get("state", {}),
            data=data.get("data", {}),
            metadata=data.get("metadata", {}),
            correlation_id=data.get("correlation_id"),
        )


@dataclass
class HookResult:
    """
    Result from hook execution.

    Hooks can modify pipeline behavior by:
    - Halting execution (halt_pipeline=True)
    - Modifying data (modify_data dict)
    - Injecting context (inject_context dict)
    - Adding defects (defects list)

    Attributes:
        success: Whether hook executed successfully
        blocking: Whether this hook blocks pipeline on failure
        halt_pipeline: Request to halt pipeline execution
        modify_data: Data modifications to apply
        inject_context: Context to inject into pipeline
        defects: Defects discovered by this hook
        error_message: Error message if execution failed
        metadata: Additional result metadata
    """

    success: bool = True
    blocking: bool = False
    halt_pipeline: bool = False
    modify_data: Optional[Dict[str, Any]] = None
    inject_context: Optional[Dict[str, Any]] = None
    defects: List[Dict[str, Any]] = field(default_factory=list)
    error_message: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "success": self.success,
            "blocking": self.blocking,
            "halt_pipeline": self.halt_pipeline,
            "modify_data": self.modify_data,
            "inject_context": self.inject_context,
            "defects_count": len(self.defects),
            "defects": self.defects,
            "error_message": self.error_message,
            "metadata": self.metadata,
        }

    @classmethod
    def success_result(
        cls,
        modify_data: Optional[Dict[str, Any]] = None,
        inject_context: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> "HookResult":
        """Create a success result with optional modifications."""
        return cls(
            success=True,
            modify_data=modify_data,
            inject_context=inject_context,
            metadata=metadata or {},
        )

    @classmethod
    def failure_result(
        cls,
        error_message: str,
        blocking: bool = False,
        halt_pipeline: bool = False,
        defects: Optional[List[Dict[str, Any]]] = None,
    ) -> "HookResult":
        """Create a failure result."""
        return cls(
            success=False,
            blocking=blocking,
            halt_pipeline=halt_pipeline,
            error_message=error_message,
            defects=defects or [],
        )


class BaseHook(ABC):
    """
    Abstract base class for all GAIA hooks.

    Hooks are executed at specific points in the pipeline lifecycle
    and can:
    - Validate preconditions (blocking)
    - Inject context
    - Modify data
    - Extract defects
    - Log events
    - Send notifications

    Subclasses must:
    1. Set class attributes: name, event, priority, blocking
    2. Implement execute() async method

    Example:
        class MyValidationHook(BaseHook):
            name = "my_validation"
            event = "AGENT_EXECUTE"
            priority = HookPriority.HIGH
            blocking = True

            async def execute(self, context: HookContext) -> HookResult:
                # Validation logic
                if not context.data.get("required_field"):
                    return HookResult.failure_result(
                        "Missing required field",
                        blocking=True,
                        halt_pipeline=True
                    )
                return HookResult.success_result()
    """

    # Hook metadata (override in subclasses)
    name: str = "base_hook"
    event: str = "*"  # Listen to all events (*) or specific event
    priority: HookPriority = HookPriority.NORMAL
    blocking: bool = False  # Whether failure blocks pipeline
    description: str = ""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initialize hook.

        Args:
            config: Optional configuration dictionary
        """
        self.config = config or {}
        self._execution_count = 0
        self._last_error: Optional[str] = None

    @property
    def execution_count(self) -> int:
        """Get number of times this hook has executed."""
        return self._execution_count

    @abstractmethod
    async def execute(self, context: HookContext) -> HookResult:
        """
        Execute the hook.

        This is the main hook method called when the event occurs.

        Args:
            context: Hook context with event data

        Returns:
            HookResult with execution outcome

        Raises:
            Exception: If hook execution fails (will be caught by executor)
        """
        pass

    async def on_before(self, context: HookContext) -> None:
        """
        Called before execute (optional hook).

        Use for setup, logging, or pre-processing.

        Args:
            context: Hook context
        """
        pass

    async def on_after(
        self,
        context: HookContext,
        result: HookResult,
    ) -> None:
        """
        Called after execute (optional hook).

        Use for cleanup, logging, or post-processing.

        Args:
            context: Hook context
            result: Hook execution result
        """
        pass

    def can_handle(self, event: str) -> bool:
        """
        Check if this hook can handle an event.

        Args:
            event: Event name

        Returns:
            True if hook should execute for this event
        """
        return self.event == "*" or self.event == event

    def get_info(self) -> Dict[str, Any]:
        """Get hook information."""
        return {
            "name": self.name,
            "event": self.event,
            "priority": self.priority.name,
            "blocking": self.blocking,
            "description": self.description,
            "execution_count": self._execution_count,
            "last_error": self._last_error,
            "config": self.config,
        }

    def _create_defect(
        self,
        description: str,
        severity: str = "medium",
        category: Optional[str] = None,
        suggestion: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Create a defect record.

        Args:
            description: Defect description
            severity: Severity level
            category: Defect category
            suggestion: Suggested fix

        Returns:
            Defect dictionary
        """
        return {
            "category": category or self.name,
            "description": description,
            "severity": severity,
            "suggestion": suggestion,
            "source": "hook",
            "hook_name": self.name,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def _increment_execution(self) -> None:
        """Increment execution count."""
        self._execution_count += 1

    def _set_error(self, error: str) -> None:
        """Set last error."""
        self._last_error = error
