# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Base step classes for orchestration workflows.

Provides unified interfaces for workflow steps with standardized results.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional, Tuple


class StepStatus(Enum):
    """Status of a step execution."""

    SUCCESS = auto()
    WARNING = auto()  # Succeeded with warnings
    ERROR = auto()
    SKIPPED = auto()  # Step was skipped (e.g., already done)


class ErrorCategory(Enum):
    """Categories of errors for recovery routing."""

    UNKNOWN = auto()
    NETWORK = auto()  # Transient network errors
    RESOURCE = auto()  # Resource unavailable (disk, memory)
    DEPENDENCY = auto()  # Missing dependency
    COMPILATION = auto()  # TypeScript, build errors
    SYNTAX = auto()  # Code syntax errors
    RUNTIME = auto()  # Runtime execution errors
    VALIDATION = auto()  # Lint, type check failures
    CONFIGURATION = auto()  # Config file issues


@dataclass
class StepResult:
    """Unified result format for all step executions.

    Replaces inconsistent patterns like:
    - {"success": True, "files": [...]}
    - {"status": "ok", "output": "..."}
    - {"has_errors": False, "return_code": 0}

    With a single consistent interface.
    """

    status: StepStatus
    message: str
    error_message: Optional[str] = None
    error_category: ErrorCategory = ErrorCategory.UNKNOWN
    output: Dict[str, Any] = field(default_factory=dict)
    retryable: bool = True

    @property
    def success(self) -> bool:
        """Check if step completed successfully (including with warnings)."""
        return self.status in (StepStatus.SUCCESS, StepStatus.WARNING)

    @property
    def error(self) -> Optional[str]:
        """Get error message (alias for error_message)."""
        return self.error_message

    @classmethod
    def ok(cls, message: str, **output) -> "StepResult":
        """Create a successful result."""
        return cls(status=StepStatus.SUCCESS, message=message, output=output)

    @classmethod
    def warning(cls, message: str, **output) -> "StepResult":
        """Create a warning result (success with caveats)."""
        return cls(status=StepStatus.WARNING, message=message, output=output)

    @classmethod
    def make_error(
        cls,
        message: str,
        error_msg: str,
        category: ErrorCategory = ErrorCategory.UNKNOWN,
        retryable: bool = True,
        **output,
    ) -> "StepResult":
        """Create an error result."""
        return cls(
            status=StepStatus.ERROR,
            message=message,
            error_message=error_msg,
            error_category=category,
            retryable=retryable,
            output=output,
        )

    @classmethod
    def skipped(cls, message: str, **output) -> "StepResult":
        """Create a skipped result."""
        return cls(status=StepStatus.SKIPPED, message=message, output=output)


@dataclass
class UserContext:
    """Context passed through workflow execution.

    Contains user request info and accumulated state from previous steps.
    """

    user_request: str
    project_dir: str
    language: str = "typescript"
    project_type: str = "fullstack"
    entity_name: Optional[str] = None  # e.g., "Todo", "User"
    schema_fields: Optional[Dict[str, str]] = None  # e.g., {"title": "string"}
    accumulated_files: Dict[str, str] = field(default_factory=dict)
    step_outputs: Dict[str, Any] = field(default_factory=dict)
    fix_feedback: List[str] = field(default_factory=list)
    validation_reports: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class BaseStep(ABC):
    """Abstract base class for workflow steps.

    Steps wrap tool invocations with standardized result handling.
    The orchestrator calls steps, which return tool invocation specs.
    The orchestrator executes the tool and passes results back to the step.
    """

    name: str
    description: str = ""

    @abstractmethod
    def get_tool_invocation(
        self, context: UserContext
    ) -> Optional[Tuple[str, Dict[str, Any]]]:
        """Return the tool name and arguments to execute.

        Args:
            context: Current workflow context with user request and state

        Returns:
            Tuple of (tool_name, tool_args) or None to skip this step
        """

    @abstractmethod
    def handle_result(self, result: Any, context: UserContext) -> StepResult:
        """Convert raw tool result to standardized StepResult.

        Args:
            result: Raw result from tool execution
            context: Current workflow context

        Returns:
            Standardized StepResult
        """

    def should_skip(self, context: UserContext) -> Optional[str]:  # noqa: ARG002
        """Check if this step should be skipped.

        Args:
            context: Current workflow context

        Returns:
            Reason string if should skip, None otherwise
        """
        # Default: don't skip. Subclasses override to add skip logic.
        del context  # Unused in base class
        return None

    def validate_preconditions(
        self, context: UserContext
    ) -> Optional[str]:  # noqa: ARG002
        """Validate that preconditions for this step are met.

        Args:
            context: Current workflow context

        Returns:
            Error message if preconditions not met, None otherwise
        """
        # Default: no preconditions. Subclasses override.
        del context  # Unused in base class
        return None


# Type alias for tool executor function
ToolExecutor = Callable[[str, Dict[str, Any]], Any]
