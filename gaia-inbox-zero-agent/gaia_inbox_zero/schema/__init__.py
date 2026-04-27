"""Result schema definitions."""

from .result_schema import (
    ModelConfig,
    ToolTiming,
    ConversationEntry,
    TaskInputs,
    TaskOutputs,
    EmailBreakdown,
    BatchMetrics,
    ExecutionDetails,
    PerformanceMetrics,
    ValidationResult,
    TaskResult,
    RunSummary,
    BenchmarkRun,
    task_result_from_legacy,
)

__all__ = [
    "ModelConfig",
    "ToolTiming",
    "ConversationEntry",
    "TaskInputs",
    "TaskOutputs",
    "EmailBreakdown",
    "BatchMetrics",
    "ExecutionDetails",
    "PerformanceMetrics",
    "ValidationResult",
    "TaskResult",
    "RunSummary",
    "BenchmarkRun",
    "task_result_from_legacy",
]
