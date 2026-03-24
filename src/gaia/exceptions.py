"""
GAIA Core Pipeline Engine - Custom Exceptions

This module defines custom exceptions for the GAIA pipeline system.
"""


class GAIAException(Exception):
    """Base exception for all GAIA-related errors."""

    def __init__(self, message: str, details: dict | None = None):
        self.message = message
        self.details = details or {}
        super().__init__(self.message)

    def to_dict(self) -> dict:
        """Convert exception to dictionary for logging."""
        return {
            "type": self.__class__.__name__,
            "message": self.message,
            "details": self.details,
        }


# =============================================================================
# Pipeline State Machine Exceptions
# =============================================================================


class InvalidStateTransition(GAIAException):
    """Raised when an invalid state transition is attempted."""

    def __init__(self, message: str, from_state: str | None = None, to_state: str | None = None):
        super().__init__(message, {"from_state": from_state, "to_state": to_state})
        self.from_state = from_state
        self.to_state = to_state


class PipelineNotInitializedError(GAIAException):
    """Raised when pipeline operations are attempted before initialization."""

    def __init__(self, message: str = "Pipeline not initialized"):
        super().__init__(message)


class PipelineAlreadyRunningError(GAIAException):
    """Raised when attempting to start a pipeline that is already running."""

    def __init__(self, message: str = "Pipeline is already running"):
        super().__init__(message)


class PipelineNotRunningError(GAIAException):
    """Raised when operations require a running pipeline but it's not running."""

    def __init__(self, message: str = "Pipeline is not running"):
        super().__init__(message)


class PipelineTerminatedError(GAIAException):
    """Raised when operations are attempted on a terminated pipeline."""

    def __init__(self, message: str = "Pipeline has terminated", reason: str | None = None):
        super().__init__(message, {"reason": reason})
        self.reason = reason


# =============================================================================
# Loop Management Exceptions
# =============================================================================


class LoopCreationError(GAIAException):
    """Raised when loop creation fails."""

    def __init__(self, message: str, config: dict | None = None):
        super().__init__(message, {"config": config})
        self.config = config


class LoopNotFoundError(GAIAException):
    """Raised when referencing a non-existent loop."""

    def __init__(self, loop_id: str):
        super().__init__(f"Loop not found: {loop_id}", {"loop_id": loop_id})
        self.loop_id = loop_id


class LoopExecutionError(GAIAException):
    """Raised when loop execution fails."""

    def __init__(self, loop_id: str, error: str):
        super().__init__(f"Loop execution failed: {error}", {"loop_id": loop_id})
        self.loop_id = loop_id
        self.execution_error = error


class LoopTimeoutError(GAIAException):
    """Raised when a loop exceeds its timeout."""

    def __init__(self, loop_id: str, timeout_seconds: int):
        super().__init__(
            f"Loop timed out after {timeout_seconds} seconds",
            {"loop_id": loop_id, "timeout_seconds": timeout_seconds},
        )
        self.loop_id = loop_id
        self.timeout_seconds = timeout_seconds


class MaxIterationsExceededError(GAIAException):
    """Raised when a loop exceeds maximum iterations."""

    def __init__(self, loop_id: str, max_iterations: int):
        super().__init__(
            f"Loop exceeded maximum iterations ({max_iterations})",
            {"loop_id": loop_id, "max_iterations": max_iterations},
        )
        self.loop_id = loop_id
        self.max_iterations = max_iterations


# =============================================================================
# Quality Scoring Exceptions
# =============================================================================


class QualityScoringError(GAIAException):
    """Raised when quality scoring fails."""

    def __init__(self, message: str, category: str | None = None):
        super().__init__(message, {"category": category})
        self.category = category


class InvalidQualityThresholdError(GAIAException):
    """Raised when an invalid quality threshold is provided."""

    def __init__(self, threshold: float):
        super().__init__(
            f"Invalid quality threshold: {threshold}. Must be between 0 and 1.",
            {"threshold": threshold},
        )
        self.threshold = threshold


class ValidatorNotFoundError(GAIAException):
    """Raised when a validator is not found for a category."""

    def __init__(self, category_id: str):
        super().__init__(f"Validator not found for category: {category_id}", {"category_id": category_id})
        self.category_id = category_id


class QualityGateFailedError(GAIAException):
    """Raised when quality gate validation fails."""

    def __init__(
        self,
        phase: str,
        score: float,
        threshold: float,
        defects: list | None = None,
    ):
        super().__init__(
            f"Quality gate failed for phase '{phase}': score {score:.2f} < threshold {threshold:.2f}",
            {
                "phase": phase,
                "score": score,
                "threshold": threshold,
                "defects": defects or [],
            },
        )
        self.phase = phase
        self.score = score
        self.threshold = threshold
        self.defects = defects or []


# =============================================================================
# Agent Registry Exceptions
# =============================================================================


class AgentNotFoundError(GAIAException):
    """Raised when an agent is not found in the registry."""

    def __init__(self, agent_id: str):
        super().__init__(f"Agent not found: {agent_id}", {"agent_id": agent_id})
        self.agent_id = agent_id


class AgentLoadError(GAIAException):
    """Raised when agent loading fails."""

    def __init__(self, file_path: str, error: str):
        super().__init__(f"Failed to load agent from {file_path}: {error}", {"file_path": file_path})
        self.file_path = file_path
        self.load_error = error


class AgentSelectionError(GAIAException):
    """Raised when agent selection fails."""

    def __init__(self, message: str, task: str | None = None):
        super().__init__(message, {"task": task})
        self.task = task


class AgentExecutionError(GAIAException):
    """Raised when agent execution fails."""

    def __init__(self, agent_id: str, error: str):
        super().__init__(f"Agent execution failed: {error}", {"agent_id": agent_id})
        self.agent_id = agent_id
        self.execution_error = error


# =============================================================================
# Hook System Exceptions
# =============================================================================


class HookRegistrationError(GAIAException):
    """Raised when hook registration fails."""

    def __init__(self, hook_name: str, error: str):
        super().__init__(f"Failed to register hook '{hook_name}': {error}", {"hook_name": hook_name})
        self.hook_name = hook_name
        self.registration_error = error


class HookExecutionError(GAIAException):
    """Raised when hook execution fails."""

    def __init__(self, hook_name: str, event: str, error: str):
        super().__init__(
            f"Hook '{hook_name}' failed on event '{event}': {error}",
            {"hook_name": hook_name, "event": event},
        )
        self.hook_name = hook_name
        self.event = event
        self.execution_error = error


class HookHaltPipelineError(GAIAException):
    """Raised when a blocking hook requests pipeline halt."""

    def __init__(self, hook_name: str, reason: str):
        super().__init__(
            f"Pipeline halted by hook '{hook_name}': {reason}",
            {"hook_name": hook_name, "reason": reason},
        )
        self.hook_name = hook_name
        self.reason = reason


# =============================================================================
# Configuration Exceptions
# =============================================================================


class ConfigurationError(GAIAException):
    """Raised when configuration is invalid or missing."""

    def __init__(self, message: str, config_key: str | None = None):
        super().__init__(message, {"config_key": config_key})
        self.config_key = config_key


class TemplateNotFoundError(GAIAException):
    """Raised when a quality template is not found."""

    def __init__(self, template_name: str):
        super().__init__(f"Template not found: {template_name}", {"template_name": template_name})
        self.template_name = template_name


# =============================================================================
# Chronicle Exceptions
# =============================================================================


class ChronicleError(GAIAException):
    """Base exception for chronicle-related errors."""

    pass


class ChronicleEntryError(ChronicleError):
    """Raised when chronicle entry operations fail."""

    pass


class ChronicleCompactionError(ChronicleError):
    """Raised when chronicle compaction fails."""

    pass
