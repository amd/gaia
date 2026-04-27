"""
Result Schema Unit Tests

Tests for gaia_inbox_zero/schema/result_schema.py covering:
- All dataclass constructors
- to_dict() serialization methods
- task_result_from_legacy conversion
- Edge cases and None handling
"""

import json
import pytest
from datetime import datetime

from gaia_inbox_zero.schema.result_schema import (
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


# -- ModelConfig Tests -------------------------------------------------------

class TestModelConfig:
    """Tests for ModelConfig dataclass."""

    def test_minimal_construction(self):
        """Should create with just required fields."""
        config = ModelConfig(model_id="test-model", provider="lemonade")
        assert config.model_id == "test-model"
        assert config.provider == "lemonade"
        assert config.max_tokens is None
        assert config.temperature is None
        assert config.top_p is None
        assert config.extra_params == {}

    def test_full_construction(self):
        """Should create with all fields."""
        config = ModelConfig(
            model_id="gpt-4",
            provider="anthropic",
            max_tokens=4096,
            temperature=0.7,
            top_p=0.9,
            extra_params={"stop": ["\n"]},
        )
        assert config.max_tokens == 4096
        assert config.temperature == 0.7
        assert config.top_p == 0.9
        assert config.extra_params == {"stop": ["\n"]}

    def test_extra_params_default_empty(self):
        """extra_params should default to empty dict, not None."""
        c1 = ModelConfig(model_id="a", provider="b")
        c2 = ModelConfig(model_id="a", provider="b")
        assert c1.extra_params is not None
        c1.extra_params["key"] = "value"
        assert "key" not in c2.extra_params  # No shared state


# -- ToolTiming Tests --------------------------------------------------------

class TestToolTiming:
    """Tests for ToolTiming dataclass."""

    def test_minimal_construction(self):
        """Should create with required fields."""
        timing = ToolTiming(tool_name="test_tool", start_time=0.0, end_time=1.0, duration_ms=1000)
        assert timing.tool_name == "test_tool"
        assert timing.input_tokens == 0
        assert timing.output_tokens == 0
        assert timing.result_preview == ""

    def test_full_construction(self):
        """Should accept all fields."""
        timing = ToolTiming(
            tool_name="fetch_emails",
            start_time=1000.0,
            end_time=1005.5,
            duration_ms=5500,
            input_tokens=500,
            output_tokens=200,
            result_preview="Fetched 20 emails",
        )
        assert timing.duration_ms == 5500
        assert timing.input_tokens == 500


# -- ConversationEntry Tests -------------------------------------------------

class TestConversationEntry:
    """Tests for ConversationEntry dataclass."""

    def test_minimal(self):
        """Should create with role and content."""
        entry = ConversationEntry(role="user", content="Hello")
        assert entry.role == "user"
        assert entry.content == "Hello"
        assert entry.timestamp is None
        assert entry.tool_call_id is None
        assert entry.metadata == {}

    def test_with_metadata(self):
        """Should accept metadata dict."""
        entry = ConversationEntry(
            role="tool",
            content={"result": "success"},
            tool_call_id="call-123",
            metadata={"tool_name": "fetch_emails"},
        )
        assert entry.metadata["tool_name"] == "fetch_emails"

    def test_content_can_be_dict(self):
        """Content field should accept dict type."""
        entry = ConversationEntry(role="assistant", content={"key": "value"})
        assert isinstance(entry.content, dict)


# -- TaskInputs Tests --------------------------------------------------------

class TestTaskInputs:
    """Tests for TaskInputs dataclass."""

    def test_minimal(self):
        """Should create with required fields."""
        inputs = TaskInputs(
            system_prompt="You are a helper",
            user_prompt="Classify these emails",
            model_config=ModelConfig(model_id="test", provider="lemonade"),
        )
        assert inputs.system_prompt == "You are a helper"
        assert inputs.pre_fetched_data is None
        assert inputs.additional_context == {}

    def test_with_pre_fetched_data(self):
        """Should accept pre_fetched_data."""
        inputs = TaskInputs(
            system_prompt="System",
            user_prompt="User",
            model_config=ModelConfig(model_id="x", provider="y"),
            pre_fetched_data={"emails": [{"id": 1}]},
            additional_context={"batch_size": 20},
        )
        assert inputs.pre_fetched_data["emails"][0]["id"] == 1


# -- TaskOutputs Tests -------------------------------------------------------

class TestTaskOutputs:
    """Tests for TaskOutputs dataclass."""

    def test_minimal(self):
        """Should create with required fields."""
        outputs = TaskOutputs(
            final_response="Done",
            conversation=[],
            tools_used=[],
            steps_taken=1,
            success=True,
        )
        assert outputs.success is True
        assert outputs.steps_taken == 1


# -- EmailBreakdown Tests ----------------------------------------------------

class TestEmailBreakdown:
    """Tests for EmailBreakdown dataclass."""

    def test_minimal(self):
        """Should create with required fields."""
        eb = EmailBreakdown(email_id="mbox-001", subject="Test", category="FYI")
        assert eb.load_time_ms is None
        assert eb.processing_notes == ""

    def test_full(self):
        """Should accept all fields."""
        eb = EmailBreakdown(
            email_id="mbox-001",
            subject="Urgent meeting",
            category="URGENT",
            load_time_ms=45,
            processing_notes="Classified via LLM",
        )
        assert eb.load_time_ms == 45


# -- BatchMetrics Tests ------------------------------------------------------

class TestBatchMetrics:
    """Tests for BatchMetrics dataclass."""

    def test_construction(self):
        """Should create with all fields."""
        bm = BatchMetrics(
            batch_num=1,
            total_batches=5,
            email_count=20,
            est_tokens=5000,
            duration_ms=30000,
            duration_min=0.5,
            input_tokens=4000,
            output_tokens=1000,
            total_tokens=5000,
            steps=3,
            categories="URGENT, FYI",
            status="success",
        )
        assert bm.batch_num == 1
        assert bm.total_batches == 5
        assert bm.status == "success"


# -- ExecutionDetails Tests --------------------------------------------------

class TestExecutionDetails:
    """Tests for ExecutionDetails dataclass."""

    def test_minimal(self):
        """Should create with required per_tool_timing."""
        ed = ExecutionDetails(per_tool_timing=[])
        assert ed.per_item_breakdown is None
        assert ed.per_batch_metrics is None
        assert ed.memory_usage_mb is None
        assert ed.errors_encountered == []

    def test_with_email_breakdown(self):
        """Should accept per_item_breakdown."""
        ed = ExecutionDetails(
            per_tool_timing=[],
            per_item_breakdown=[
                EmailBreakdown(email_id="mbox-001", subject="Test", category="FYI"),
            ],
        )
        assert len(ed.per_item_breakdown) == 1


# -- PerformanceMetrics Tests ------------------------------------------------

class TestPerformanceMetrics:
    """Tests for PerformanceMetrics dataclass."""

    def test_construction(self):
        """Should create with all fields."""
        pm = PerformanceMetrics(
            latency_ms=5000,
            tokens_in=1000,
            tokens_out=200,
            tokens_total=1200,
            tokens_per_second=240.0,
            ttft_ms=500,
        )
        assert pm.latency_ms == 5000
        assert pm.tokens_per_second == 240.0

    def test_ttft_optional(self):
        """ttft_ms should be optional."""
        pm = PerformanceMetrics(
            latency_ms=3000,
            tokens_in=500,
            tokens_out=100,
            tokens_total=600,
            tokens_per_second=200.0,
        )
        assert pm.ttft_ms is None


# -- ValidationResult Tests --------------------------------------------------

class TestValidationResult:
    """Tests for ValidationResult dataclass."""

    def test_passed(self):
        """Should create passed validation."""
        vr = ValidationResult(passed=True)
        assert vr.passed is True
        assert vr.reason is None

    def test_failed(self):
        """Should create failed validation with reason."""
        vr = ValidationResult(
            passed=False,
            reason="No categories found",
            validator_name="category_check",
            details={"expected": 5, "found": 0},
        )
        assert vr.passed is False
        assert vr.details["expected"] == 5


# -- TaskResult Tests --------------------------------------------------------

class TestTaskResult:
    """Tests for TaskResult dataclass and to_dict()."""

    def _make_task_result(self, **overrides):
        """Helper to create a TaskResult with sensible defaults."""
        defaults = dict(
            task_name="inbox-zero",
            task_category="email",
            clawflow="inbox-zero-helper",
            agent="InboxZeroAgent",
            run_id="run-123",
            inputs=TaskInputs(
                system_prompt="You are a helper",
                user_prompt="Classify emails",
                model_config=ModelConfig(model_id="test-model", provider="lemonade"),
            ),
            outputs=TaskOutputs(
                final_response="All emails classified",
                conversation=[
                    ConversationEntry(role="user", content="Classify emails"),
                    ConversationEntry(role="assistant", content="Done"),
                ],
                tools_used=["fetch_unread_emails", "group_by_category"],
                steps_taken=3,
                success=True,
            ),
            execution=ExecutionDetails(
                per_tool_timing=[
                    ToolTiming(
                        tool_name="fetch_unread_emails",
                        start_time=0.0,
                        end_time=1.0,
                        duration_ms=1000,
                    ),
                ],
                per_item_breakdown=[
                    EmailBreakdown(email_id="mbox-001", subject="Test", category="FYI"),
                ],
            ),
            performance=PerformanceMetrics(
                latency_ms=5000,
                tokens_in=1000,
                tokens_out=200,
                tokens_total=1200,
                tokens_per_second=240.0,
            ),
            validation=ValidationResult(passed=True),
            timestamp="2024-01-01T00:00:00Z",
        )
        defaults.update(overrides)
        return TaskResult(**defaults)

    def test_construction(self):
        """Should create TaskResult with all fields."""
        tr = self._make_task_result()
        assert tr.task_name == "inbox-zero"
        assert tr.outputs.success is True
        assert tr.validation.passed is True

    def test_to_dict_returns_dict(self):
        """to_dict should return a dictionary."""
        tr = self._make_task_result()
        result = tr.to_dict()
        assert isinstance(result, dict)

    def test_to_dict_is_json_serializable(self):
        """to_dict output should be JSON serializable."""
        tr = self._make_task_result()
        json_str = json.dumps(tr.to_dict())
        assert isinstance(json_str, str)

    def test_to_dict_has_all_sections(self):
        """to_dict should contain all major sections."""
        tr = self._make_task_result()
        d = tr.to_dict()
        assert "task_name" in d
        assert "inputs" in d
        assert "outputs" in d
        assert "execution" in d
        assert "performance" in d
        assert "validation" in d
        assert "timestamp" in d

    def test_to_dict_inputs_structure(self):
        """to_dict inputs should have correct structure."""
        tr = self._make_task_result()
        d = tr.to_dict()
        inputs = d["inputs"]
        assert "system_prompt" in inputs
        assert "user_prompt" in inputs
        assert "model_config" in inputs
        assert inputs["model_config"]["model_id"] == "test-model"

    def test_to_dict_outputs_structure(self):
        """to_dict outputs should have correct structure."""
        tr = self._make_task_result()
        d = tr.to_dict()
        outputs = d["outputs"]
        assert "final_response" in outputs
        assert "conversation" in outputs
        assert "tools_used" in outputs
        assert "steps_taken" in outputs
        assert "success" in outputs
        assert len(outputs["conversation"]) == 2

    def test_to_dict_truncates_result_preview(self):
        """to_dict should truncate result_preview to 200 chars."""
        long_preview = "X" * 500
        tr = self._make_task_result(
            execution=ExecutionDetails(
                per_tool_timing=[
                    ToolTiming(
                        tool_name="test",
                        start_time=0,
                        end_time=1,
                        duration_ms=1000,
                        result_preview=long_preview,
                    ),
                ],
            ),
        )
        d = tr.to_dict()
        preview = d["execution"]["per_tool_timing"][0]["result_preview"]
        assert len(preview) <= 200

    def test_to_dict_per_item_breakdown(self):
        """to_dict should serialize per_item_breakdown correctly."""
        tr = self._make_task_result()
        d = tr.to_dict()
        breakdown = d["execution"]["per_item_breakdown"]
        assert len(breakdown) == 1
        assert breakdown[0]["email_id"] == "mbox-001"
        assert breakdown[0]["category"] == "FYI"

    def test_to_dict_null_per_item_breakdown(self):
        """to_dict should return None for empty per_item_breakdown."""
        tr = self._make_task_result(
            execution=ExecutionDetails(per_tool_timing=[]),
        )
        d = tr.to_dict()
        assert d["execution"]["per_item_breakdown"] is None

    def test_to_dict_null_per_batch_metrics(self):
        """to_dict should return None for empty per_batch_metrics."""
        tr = self._make_task_result(
            execution=ExecutionDetails(per_tool_timing=[]),
        )
        d = tr.to_dict()
        assert d["execution"]["per_batch_metrics"] is None

    def test_to_dict_per_batch_metrics(self):
        """to_dict should serialize per_batch_metrics correctly."""
        tr = self._make_task_result(
            execution=ExecutionDetails(
                per_tool_timing=[],
                per_batch_metrics=[
                    BatchMetrics(
                        batch_num=1,
                        total_batches=5,
                        email_count=20,
                        est_tokens=5000,
                        duration_ms=30000,
                        duration_min=0.5,
                        input_tokens=4000,
                        output_tokens=1000,
                        total_tokens=5000,
                        steps=3,
                        categories="URGENT, FYI",
                        status="success",
                    ),
                ],
            ),
        )
        d = tr.to_dict()
        metrics = d["execution"]["per_batch_metrics"]
        assert len(metrics) == 1
        assert metrics[0]["batch_num"] == 1
        assert metrics[0]["status"] == "success"


# -- RunSummary Tests --------------------------------------------------------

class TestRunSummary:
    """Tests for RunSummary dataclass."""

    def test_construction(self):
        """Should create RunSummary with all fields."""
        rs = RunSummary(
            total_tasks=5,
            passed_tasks=4,
            failed_tasks=1,
            skipped_tasks=0,
            total_tokens=10000,
            total_tokens_in=8000,
            total_tokens_out=2000,
            avg_latency_ms=5000,
            avg_tokens_per_sec=200.0,
            total_duration_ms=25000,
            judged_pass_rate=0.8,
            token_efficiency=0.2,
            savings_vs_gpt4o_usd=1.50,
            savings_vs_claude_sonnet_usd=0.75,
        )
        assert rs.total_tasks == 5
        assert rs.passed_tasks == 4
        assert rs.judged_pass_rate == 0.8


# -- BenchmarkRun Tests ------------------------------------------------------

class TestBenchmarkRun:
    """Tests for BenchmarkRun dataclass and to_dict()."""

    def _make_benchmark_run(self, **overrides):
        """Helper to create a BenchmarkRun."""
        defaults = dict(
            schema_version="2.0",
            run_id="run-123",
            model="Qwen3.5-4B-GGUF",
            provider="lemonade",
            timestamp_start="2024-01-01T00:00:00Z",
            timestamp_end="2024-01-01T00:05:00Z",
            duration_ms=300000,
            repetitions=1,
            task_count=1,
            summary=RunSummary(
                total_tasks=1,
                passed_tasks=1,
                failed_tasks=0,
                skipped_tasks=0,
                total_tokens=5000,
                total_tokens_in=4000,
                total_tokens_out=1000,
                avg_latency_ms=300000,
                avg_tokens_per_sec=16.67,
                total_duration_ms=300000,
                judged_pass_rate=1.0,
                token_efficiency=0.2,
                savings_vs_gpt4o_usd=0.5,
                savings_vs_claude_sonnet_usd=0.25,
            ),
            tasks=[],
        )
        defaults.update(overrides)
        return BenchmarkRun(**defaults)

    def test_construction(self):
        """Should create BenchmarkRun with all fields."""
        br = self._make_benchmark_run()
        assert br.schema_version == "2.0"
        assert br.model == "Qwen3.5-4B-GGUF"

    def test_to_dict_returns_dict(self):
        """to_dict should return a dictionary."""
        br = self._make_benchmark_run()
        result = br.to_dict()
        assert isinstance(result, dict)

    def test_to_dict_is_json_serializable(self):
        """to_dict output should be JSON serializable."""
        br = self._make_benchmark_run()
        json_str = json.dumps(br.to_dict())
        assert isinstance(json_str, str)

    def test_to_dict_has_all_sections(self):
        """to_dict should contain all major sections."""
        br = self._make_benchmark_run()
        d = br.to_dict()
        assert "schema_version" in d
        assert "run_id" in d
        assert "model" in d
        assert "provider" in d
        assert "summary" in d
        assert "tasks" in d

    def test_to_dict_summary_field_mapping(self):
        """to_dict should map RunSummary fields correctly."""
        br = self._make_benchmark_run()
        d = br.to_dict()
        s = d["summary"]
        # RunSummary uses total_tasks -> to_dict maps to "total"
        assert s["total"] == 1
        assert s["passed"] == 1
        assert s["failed"] == 0

    def test_to_dict_tasks_serialization(self):
        """to_dict should serialize task list."""
        br = self._make_benchmark_run(tasks=[])
        d = br.to_dict()
        assert d["tasks"] == []

    def test_to_dict_optional_fields(self):
        """to_dict should include optional fields."""
        br = self._make_benchmark_run(
            mbox_path="/path/to/mbox",
            mbox_email_count=1000,
            gaia_kpis={"accuracy": 0.95},
            config_snapshot={"batch_size": 20},
        )
        d = br.to_dict()
        assert d["mbox_path"] == "/path/to/mbox"
        assert d["mbox_email_count"] == 1000
        assert d["gaia_kpis"]["accuracy"] == 0.95

    def test_to_dict_empty_tasks(self):
        """to_dict should handle empty tasks list."""
        br = self._make_benchmark_run(tasks=[])
        d = br.to_dict()
        assert d["tasks"] == []


# -- task_result_from_legacy Tests -------------------------------------------

class TestTaskResultFromLegacy:
    """Tests for the task_result_from_legacy conversion function."""

    def test_basic_conversion(self):
        """Should convert a minimal legacy result."""
        legacy = {
            "task_name": "inbox-zero",
            "task_category": "email",
            "clawflow": "inbox-zero-helper",
            "agent": "InboxZeroAgent",
            "prompt": "Classify emails",
            "response": "All done",
            "pass": True,
            "latency_ms": 5000,
            "tokens_in": 1000,
            "tokens_out": 200,
            "tokens_total": 1200,
            "tps": 240.0,
            "timestamp": "2024-01-01T00:00:00Z",
        }

        result = task_result_from_legacy(legacy, run_id="run-1", model_id="test-model")

        assert result.task_name == "inbox-zero"
        assert result.run_id == "run-1"
        assert result.inputs.model_config.model_id == "test-model"
        assert result.inputs.model_config.provider == "lemonade"
        assert result.outputs.final_response == "All done"
        assert result.performance.latency_ms == 5000
        assert result.validation.passed is True

    def test_custom_provider(self):
        """Should accept custom provider."""
        legacy = {"prompt": "test", "response": "ok", "pass": True}
        result = task_result_from_legacy(legacy, run_id="r1", model_id="m1", provider="openai")
        assert result.inputs.model_config.provider == "openai"

    def test_conversation_conversion(self):
        """Should convert legacy conversation entries."""
        legacy = {
            "prompt": "test",
            "response": "ok",
            "pass": True,
            "conversation": [
                {"role": "user", "content": "Hello", "timestamp": "2024-01-01T00:00:00Z"},
                {"role": "assistant", "content": "Hi there"},
            ],
        }

        result = task_result_from_legacy(legacy, run_id="r1", model_id="m1")

        assert len(result.outputs.conversation) == 2
        assert result.outputs.conversation[0].role == "user"
        assert result.outputs.conversation[1].role == "assistant"

    def test_missing_fields_defaults(self):
        """Should handle missing fields with defaults."""
        legacy = {}
        result = task_result_from_legacy(legacy, run_id="r1", model_id="m1")

        assert result.task_name == ""
        assert result.outputs.final_response == ""
        assert result.outputs.success is False
        assert result.performance.latency_ms == 0
        assert result.validation.passed is False

    def test_fail_reason_mapping(self):
        """Should map fail_reason from legacy result."""
        legacy = {
            "pass": False,
            "fail_reason": "No response received",
        }
        result = task_result_from_legacy(legacy, run_id="r1", model_id="m1")

        assert result.fail_reason == "No response received"
        assert result.validation.reason == "No response received"

    def test_tools_used_extraction(self):
        """Should extract tools_used from legacy result."""
        legacy = {
            "tools_used": ["fetch_emails", "classify"],
            "pass": True,
        }
        result = task_result_from_legacy(legacy, run_id="r1", model_id="m1")

        assert result.outputs.tools_used == ["fetch_emails", "classify"]

    def test_steps_extraction(self):
        """Should extract steps from legacy result."""
        legacy = {"steps": 5, "pass": True}
        result = task_result_from_legacy(legacy, run_id="r1", model_id="m1")
        assert result.outputs.steps_taken == 5

    def test_ttft_extraction(self):
        """Should extract ttft_ms from legacy result."""
        legacy = {"ttft_ms": 300, "pass": True}
        result = task_result_from_legacy(legacy, run_id="r1", model_id="m1")
        assert result.performance.ttft_ms == 300

    def test_raw_result_preservation(self):
        """Should preserve original legacy result in raw_result."""
        legacy = {"custom_field": "value", "pass": True}
        result = task_result_from_legacy(legacy, run_id="r1", model_id="m1")

        assert result.raw_result["custom_field"] == "value"
