"""
Result Schema Definitions for GAIA Inbox Zero Benchmark

Defines comprehensive dataclasses for capturing full benchmark execution data
including inputs, outputs, conversation history, and per-tool timing.

Schema Version: 2.0
"""

from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional
from datetime import datetime
import json


@dataclass
class ModelConfig:
    """Model configuration used for the run."""
    model_id: str
    provider: str
    max_tokens: Optional[int] = None
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    extra_params: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolTiming:
    """Timing information for a single tool execution."""
    tool_name: str
    start_time: float
    end_time: float
    duration_ms: int
    input_tokens: int = 0
    output_tokens: int = 0
    result_preview: str = ""


@dataclass
class ConversationEntry:
    """A single entry in the conversation history."""
    role: str  # "user", "assistant", "tool", "system"
    content: Any  # str or dict
    timestamp: Optional[str] = None
    tool_call_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TaskInputs:
    """Complete inputs for a task execution."""
    system_prompt: str
    user_prompt: str
    model_config: ModelConfig
    pre_fetched_data: Optional[Dict[str, Any]] = None
    additional_context: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TaskOutputs:
    """Complete outputs from a task execution."""
    final_response: str
    conversation: List[ConversationEntry]
    tools_used: List[str]
    steps_taken: int
    success: bool


@dataclass
class EmailBreakdown:
    """Per-email timing and processing breakdown for inbox-zero tasks."""
    email_id: str
    subject: str
    category: str
    load_time_ms: Optional[int] = None
    processing_notes: str = ""


@dataclass
class BatchMetrics:
    """Per-batch metrics for batch-processed inbox-zero tasks."""
    batch_num: int  # 1-indexed batch number
    total_batches: int  # Total number of batches
    email_count: int  # Number of emails in this batch
    est_tokens: int  # Estimated tokens for this batch
    duration_ms: int  # Duration for this batch
    duration_min: float  # Duration in minutes (rounded)
    input_tokens: int  # Input tokens for this batch
    output_tokens: int  # Output tokens for this batch
    total_tokens: int  # Total tokens for this batch
    steps: int  # Steps taken in this batch
    categories: str  # Comma-separated categories found
    status: str  # "success", "failed", "empty"


@dataclass
class ExecutionDetails:
    """Detailed execution information for a task."""
    per_tool_timing: List[ToolTiming]
    per_item_breakdown: Optional[List[EmailBreakdown]] = None
    per_batch_metrics: Optional[List[BatchMetrics]] = None  # For batch-processed tasks
    memory_usage_mb: Optional[float] = None
    errors_encountered: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class PerformanceMetrics:
    """Performance metrics for a task or run."""
    latency_ms: int
    tokens_in: int
    tokens_out: int
    tokens_total: int
    tokens_per_second: float
    ttft_ms: Optional[int] = None  # Time to first token


@dataclass
class ValidationResult:
    """Result of task validation."""
    passed: bool
    reason: Optional[str] = None
    validator_name: str = ""
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TaskResult:
    """Complete result for a single task execution."""
    # Identification
    task_name: str
    task_category: str
    clawflow: str
    agent: str
    run_id: str

    # Inputs and Outputs
    inputs: TaskInputs
    outputs: TaskOutputs

    # Execution
    execution: ExecutionDetails
    performance: PerformanceMetrics

    # Validation
    validation: ValidationResult

    # Metadata
    timestamp: str
    fail_reason: Optional[str] = None
    raw_result: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "task_name": self.task_name,
            "task_category": self.task_category,
            "clawflow": self.clawflow,
            "agent": self.agent,
            "run_id": self.run_id,
            "inputs": {
                "system_prompt": self.inputs.system_prompt,
                "user_prompt": self.inputs.user_prompt,
                "model_config": {
                    "model_id": self.inputs.model_config.model_id,
                    "provider": self.inputs.model_config.provider,
                    "max_tokens": self.inputs.model_config.max_tokens,
                    "temperature": self.inputs.model_config.temperature,
                    "extra_params": self.inputs.model_config.extra_params,
                },
                "pre_fetched_data": self.inputs.pre_fetched_data,
                "additional_context": self.inputs.additional_context,
            },
            "outputs": {
                "final_response": self.outputs.final_response,
                "conversation": [
                    {
                        "role": entry.role,
                        "content": entry.content,
                        "timestamp": entry.timestamp,
                        "tool_call_id": entry.tool_call_id,
                        "metadata": entry.metadata,
                    }
                    for entry in self.outputs.conversation
                ],
                "tools_used": self.outputs.tools_used,
                "steps_taken": self.outputs.steps_taken,
                "success": self.outputs.success,
            },
            "execution": {
                "per_tool_timing": [
                    {
                        "tool_name": tt.tool_name,
                        "start_time": tt.start_time,
                        "end_time": tt.end_time,
                        "duration_ms": tt.duration_ms,
                        "input_tokens": tt.input_tokens,
                        "output_tokens": tt.output_tokens,
                        "result_preview": tt.result_preview[:200] if tt.result_preview else "",
                    }
                    for tt in self.execution.per_tool_timing
                ],
                "per_item_breakdown": (
                    [
                        {
                            "email_id": eb.email_id,
                            "subject": eb.subject,
                            "category": eb.category,
                            "load_time_ms": eb.load_time_ms,
                            "processing_notes": eb.processing_notes,
                        }
                        for eb in self.execution.per_item_breakdown
                    ]
                    if self.execution.per_item_breakdown
                    else None
                ),
                "per_batch_metrics": (
                    [
                        {
                            "batch_num": bm.batch_num,
                            "total_batches": bm.total_batches,
                            "email_count": bm.email_count,
                            "est_tokens": bm.est_tokens,
                            "duration_ms": bm.duration_ms,
                            "duration_min": bm.duration_min,
                            "input_tokens": bm.input_tokens,
                            "output_tokens": bm.output_tokens,
                            "total_tokens": bm.total_tokens,
                            "steps": bm.steps,
                            "categories": bm.categories,
                            "status": bm.status,
                        }
                        for bm in self.execution.per_batch_metrics
                    ]
                    if self.execution.per_batch_metrics
                    else None
                ),
                "memory_usage_mb": self.execution.memory_usage_mb,
                "errors_encountered": self.execution.errors_encountered,
            },
            "performance": {
                "latency_ms": self.performance.latency_ms,
                "tokens_in": self.performance.tokens_in,
                "tokens_out": self.performance.tokens_out,
                "tokens_total": self.performance.tokens_total,
                "tokens_per_second": self.performance.tokens_per_second,
                "ttft_ms": self.performance.ttft_ms,
            },
            "validation": {
                "passed": self.validation.passed,
                "reason": self.validation.reason,
                "validator_name": self.validation.validator_name,
                "details": self.validation.details,
            },
            "timestamp": self.timestamp,
            "fail_reason": self.fail_reason,
        }


@dataclass
class RunSummary:
    """Summary statistics for a complete run."""
    total_tasks: int
    passed_tasks: int
    failed_tasks: int
    skipped_tasks: int
    total_tokens: int
    total_tokens_in: int
    total_tokens_out: int
    avg_latency_ms: int
    avg_tokens_per_sec: float
    total_duration_ms: int
    judged_pass_rate: float
    token_efficiency: float
    savings_vs_gpt4o_usd: float
    savings_vs_claude_sonnet_usd: float


@dataclass
class BenchmarkRun:
    """Complete benchmark run with all task results."""
    # Metadata
    schema_version: str
    run_id: str
    model: str
    provider: str

    # Timing
    timestamp_start: str
    timestamp_end: str
    duration_ms: int

    # Configuration
    repetitions: int
    task_count: int

    # Results (required fields before optional)
    summary: RunSummary
    tasks: List[TaskResult]

    # Optional fields at the end
    mbox_path: Optional[str] = None
    mbox_email_count: Optional[int] = None
    gaia_kpis: Dict[str, Any] = field(default_factory=dict)
    config_snapshot: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "model": self.model,
            "provider": self.provider,
            "timestamp_start": self.timestamp_start,
            "timestamp_end": self.timestamp_end,
            "duration_ms": self.duration_ms,
            "repetitions": self.repetitions,
            "task_count": self.task_count,
            "mbox_path": self.mbox_path,
            "mbox_email_count": self.mbox_email_count,
            "summary": {
                "total": self.summary.total_tasks,
                "passed": self.summary.passed_tasks,
                "failed": self.summary.failed_tasks,
                "skipped": self.summary.skipped_tasks,
                "total_tokens": self.summary.total_tokens,
                "total_tokens_in": self.summary.total_tokens_in,
                "total_tokens_out": self.summary.total_tokens_out,
                "avg_latency_ms": self.summary.avg_latency_ms,
                "avg_tokens_per_sec": self.summary.avg_tokens_per_sec,
                "total_duration_ms": self.summary.total_duration_ms,
                "judged_pass_rate": self.summary.judged_pass_rate,
                "token_efficiency": self.summary.token_efficiency,
                "savings_vs_gpt4o_usd": self.summary.savings_vs_gpt4o_usd,
                "savings_vs_claude_sonnet_usd": self.summary.savings_vs_claude_sonnet_usd,
            },
            "tasks": [task.to_dict() for task in self.tasks],
            "gaia_kpis": self.gaia_kpis,
            "config_snapshot": self.config_snapshot,
        }


def task_result_from_legacy(
    legacy_result: Dict[str, Any],
    run_id: str,
    model_id: str,
    provider: str = "lemonade",
) -> TaskResult:
    """
    Convert a legacy result dict (schema v1.0) to TaskResult (schema v2.0).

    This provides backward compatibility for existing results.
    """
    # Build minimal inputs/outputs from legacy data
    inputs = TaskInputs(
        system_prompt="",  # Not available in legacy
        user_prompt=legacy_result.get("prompt", ""),
        model_config=ModelConfig(
            model_id=model_id,
            provider=provider,
        ),
    )

    # Extract conversation from legacy if available
    conversation = []
    if "conversation" in legacy_result and legacy_result["conversation"]:
        for entry in legacy_result["conversation"]:
            if isinstance(entry, dict):
                conversation.append(ConversationEntry(
                    role=entry.get("role", "assistant"),
                    content=entry.get("content", ""),
                    timestamp=entry.get("timestamp"),
                ))

    outputs = TaskOutputs(
        final_response=legacy_result.get("response", ""),
        conversation=conversation,
        tools_used=legacy_result.get("tools_used", []),
        steps_taken=legacy_result.get("steps", 0),
        success=legacy_result.get("pass", False),
    )

    execution = ExecutionDetails(
        per_tool_timing=[],  # Not available in legacy
        per_item_breakdown=None,
    )

    performance = PerformanceMetrics(
        latency_ms=legacy_result.get("latency_ms", 0),
        tokens_in=legacy_result.get("tokens_in", 0),
        tokens_out=legacy_result.get("tokens_out", 0),
        tokens_total=legacy_result.get("tokens_total", 0),
        tokens_per_second=legacy_result.get("tps", 0.0),
        ttft_ms=legacy_result.get("ttft_ms"),
    )

    validation = ValidationResult(
        passed=legacy_result.get("pass", False),
        reason=legacy_result.get("fail_reason"),
    )

    return TaskResult(
        task_name=legacy_result.get("task_name", ""),
        task_category=legacy_result.get("task_category", ""),
        clawflow=legacy_result.get("clawflow", ""),
        agent=legacy_result.get("agent", ""),
        run_id=run_id,
        inputs=inputs,
        outputs=outputs,
        execution=execution,
        performance=performance,
        validation=validation,
        timestamp=legacy_result.get("timestamp", datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")),
        fail_reason=legacy_result.get("fail_reason"),
        raw_result=legacy_result,
    )
