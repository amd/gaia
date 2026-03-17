# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for SSEOutputHandler and its helper functions.

Tests the SSE bridge that converts agent console events into typed JSON
events queued for Server-Sent Events delivery to the frontend.
"""

import queue
import time

import pytest

from gaia.ui.sse_handler import (
    SSEOutputHandler,
    _fix_double_escaped,
    _format_tool_args,
    _summarize_tool_result,
    _tool_description,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def handler():
    """Create a fresh SSEOutputHandler for each test."""
    return SSEOutputHandler()


def _drain(handler: SSEOutputHandler):
    """Drain all events from the handler's queue and return as a list."""
    events = []
    while not handler.event_queue.empty():
        events.append(handler.event_queue.get_nowait())
    return events


# ===========================================================================
# SSEOutputHandler - Initialization
# ===========================================================================


class TestSSEOutputHandlerInit:
    """Tests for SSEOutputHandler.__init__."""

    def test_event_queue_is_empty(self, handler):
        assert isinstance(handler.event_queue, queue.Queue)
        assert handler.event_queue.empty()

    def test_start_time_is_none(self, handler):
        assert handler._start_time is None

    def test_step_count_is_zero(self, handler):
        assert handler._step_count == 0

    def test_tool_count_is_zero(self, handler):
        assert handler._tool_count == 0

    def test_last_tool_name_is_none(self, handler):
        assert handler._last_tool_name is None

    def test_stream_buffer_is_empty_string(self, handler):
        assert handler._stream_buffer == ""


# ===========================================================================
# SSEOutputHandler._emit
# ===========================================================================


class TestEmit:
    """Tests for SSEOutputHandler._emit."""

    def test_emit_puts_event_on_queue(self, handler):
        event = {"type": "test", "data": 42}
        handler._emit(event)
        assert not handler.event_queue.empty()
        assert handler.event_queue.get_nowait() == event

    def test_emit_multiple_events_preserves_order(self, handler):
        events_in = [{"type": "a"}, {"type": "b"}, {"type": "c"}]
        for e in events_in:
            handler._emit(e)
        events_out = _drain(handler)
        assert events_out == events_in

    def test_emit_none_sentinel(self, handler):
        handler._emit(None)
        assert handler.event_queue.get_nowait() is None


# ===========================================================================
# SSEOutputHandler._elapsed
# ===========================================================================


class TestElapsed:
    """Tests for SSEOutputHandler._elapsed."""

    def test_elapsed_without_start_time_returns_zero(self, handler):
        assert handler._elapsed() == 0.0

    def test_elapsed_with_start_time_returns_positive(self, handler):
        handler._start_time = time.time() - 1.5
        elapsed = handler._elapsed()
        assert elapsed >= 1.4
        assert elapsed <= 2.0

    def test_elapsed_returns_rounded_value(self, handler):
        handler._start_time = time.time() - 0.123
        elapsed = handler._elapsed()
        # Result should be a float rounded to 2 decimal places
        assert elapsed == round(elapsed, 2)


# ===========================================================================
# SSEOutputHandler.print_processing_start
# ===========================================================================


class TestPrintProcessingStart:
    """Tests for SSEOutputHandler.print_processing_start."""

    def test_sets_start_time(self, handler):
        handler.print_processing_start("hello", 10)
        assert handler._start_time is not None
        assert handler._start_time <= time.time()

    def test_resets_step_count(self, handler):
        handler._step_count = 5
        handler.print_processing_start("hello", 10)
        assert handler._step_count == 0

    def test_resets_tool_count(self, handler):
        handler._tool_count = 3
        handler.print_processing_start("hello", 10)
        assert handler._tool_count == 0

    def test_suppresses_thinking_event(self, handler):
        """print_processing_start no longer emits a thinking event.

        The "Sending to <model>..." message was suppressed because the
        streaming chat flow in _chat_helpers already emits its own
        "Connecting to ..." thinking event, so the duplicate added noise.
        """
        handler.print_processing_start("hello", 10, model_id="qwen")
        events = _drain(handler)
        assert len(events) == 0

    def test_suppresses_thinking_event_no_model(self, handler):
        """Same suppression applies when no model_id is provided."""
        handler.print_processing_start("hello", 10)
        events = _drain(handler)
        assert len(events) == 0


# ===========================================================================
# SSEOutputHandler.print_step_header
# ===========================================================================


class TestPrintStepHeader:
    """Tests for SSEOutputHandler.print_step_header."""

    def test_sets_step_count(self, handler):
        handler.print_step_header(3, 10)
        assert handler._step_count == 3

    def test_emits_step_event(self, handler):
        handler.print_step_header(2, 5)
        events = _drain(handler)
        assert len(events) == 1
        assert events[0] == {
            "type": "step",
            "step": 2,
            "total": 5,
            "status": "started",
        }


# ===========================================================================
# SSEOutputHandler.print_state_info
# ===========================================================================


class TestPrintStateInfo:
    """Tests for SSEOutputHandler.print_state_info.

    print_state_info is intentionally suppressed (no-op) because the
    internal agent state labels (PLANNING, DIRECT EXECUTION, etc.)
    duplicate the thinking step that immediately follows.
    """

    def test_suppressed_no_events(self, handler):
        handler.print_state_info("Analyzing document...")
        events = _drain(handler)
        assert len(events) == 0


# ===========================================================================
# SSEOutputHandler.print_thought
# ===========================================================================


class TestPrintThought:
    """Tests for SSEOutputHandler.print_thought."""

    def test_emits_thinking_event(self, handler):
        handler.print_thought("I should search for files first")
        events = _drain(handler)
        assert len(events) == 1
        assert events[0] == {
            "type": "thinking",
            "content": "I should search for files first",
        }


# ===========================================================================
# SSEOutputHandler.print_goal
# ===========================================================================


class TestPrintGoal:
    """Tests for SSEOutputHandler.print_goal."""

    def test_emits_status_when_goal_is_truthy(self, handler):
        handler.print_goal("Find relevant code")
        events = _drain(handler)
        assert len(events) == 1
        assert events[0] == {
            "type": "status",
            "status": "working",
            "message": "Find relevant code",
        }

    def test_no_event_when_goal_is_empty_string(self, handler):
        handler.print_goal("")
        events = _drain(handler)
        assert len(events) == 0

    def test_no_event_when_goal_is_none(self, handler):
        handler.print_goal(None)
        events = _drain(handler)
        assert len(events) == 0


# ===========================================================================
# SSEOutputHandler.print_plan
# ===========================================================================


class TestPrintPlan:
    """Tests for SSEOutputHandler.print_plan."""

    def test_plan_with_tool_dicts(self, handler):
        plan = [
            {"tool": "search_file", "tool_args": {"query": "main"}},
            {"tool": "read_file"},
        ]
        handler.print_plan(plan, current_step=0)
        events = _drain(handler)
        assert len(events) == 1
        event = events[0]
        assert event["type"] == "plan"
        assert event["current_step"] == 0
        assert "search_file" in event["steps"][0]
        assert "query='main'" in event["steps"][0]
        assert event["steps"][1] == "read_file"

    def test_plan_with_tool_dict_no_args(self, handler):
        plan = [{"tool": "list_files"}]
        handler.print_plan(plan)
        events = _drain(handler)
        assert events[0]["steps"] == ["list_files"]

    def test_plan_with_tool_dict_empty_args(self, handler):
        plan = [{"tool": "list_files", "tool_args": {}}]
        handler.print_plan(plan)
        events = _drain(handler)
        # Empty tool_args is falsy, so no args_str appended
        assert events[0]["steps"] == ["list_files"]

    def test_plan_with_non_tool_dicts(self, handler):
        plan = [{"action": "think", "reason": "analyze"}]
        handler.print_plan(plan)
        events = _drain(handler)
        # Non-tool dicts are json-serialized
        step_str = events[0]["steps"][0]
        assert '"action"' in step_str
        assert '"think"' in step_str

    def test_plan_with_strings(self, handler):
        plan = ["Step 1: Search files", "Step 2: Analyze"]
        handler.print_plan(plan)
        events = _drain(handler)
        assert events[0]["steps"] == ["Step 1: Search files", "Step 2: Analyze"]

    def test_plan_with_mixed_types(self, handler):
        plan = [
            {"tool": "search_file"},
            "Analyze results",
            42,
        ]
        handler.print_plan(plan)
        events = _drain(handler)
        steps = events[0]["steps"]
        assert steps[0] == "search_file"
        assert steps[1] == "Analyze results"
        assert steps[2] == "42"

    def test_plan_current_step_none_by_default(self, handler):
        handler.print_plan(["a"])
        events = _drain(handler)
        assert events[0]["current_step"] is None

    def test_plan_with_multiple_tool_args(self, handler):
        plan = [
            {
                "tool": "search_file",
                "tool_args": {"query": "test", "directory": "/src"},
            }
        ]
        handler.print_plan(plan)
        events = _drain(handler)
        step = events[0]["steps"][0]
        assert "query='test'" in step
        assert "directory='/src'" in step


# ===========================================================================
# SSEOutputHandler.print_tool_usage
# ===========================================================================


class TestPrintToolUsage:
    """Tests for SSEOutputHandler.print_tool_usage."""

    def test_increments_tool_count(self, handler):
        handler.print_tool_usage("search_file")
        assert handler._tool_count == 1
        handler.print_tool_usage("read_file")
        assert handler._tool_count == 2

    def test_sets_last_tool_name(self, handler):
        handler.print_tool_usage("search_file")
        assert handler._last_tool_name == "search_file"
        handler.print_tool_usage("read_file")
        assert handler._last_tool_name == "read_file"

    def test_emits_tool_start_event(self, handler):
        handler.print_tool_usage("search_file")
        events = _drain(handler)
        assert len(events) == 1
        assert events[0] == {
            "type": "tool_start",
            "tool": "search_file",
            "detail": _tool_description("search_file"),
        }

    def test_emits_tool_start_unknown_tool_empty_detail(self, handler):
        handler.print_tool_usage("unknown_tool_xyz")
        events = _drain(handler)
        assert events[0]["detail"] == ""


# ===========================================================================
# SSEOutputHandler.print_tool_complete
# ===========================================================================


class TestPrintToolComplete:
    """Tests for SSEOutputHandler.print_tool_complete."""

    def test_emits_tool_end_event(self, handler):
        handler.print_tool_complete()
        events = _drain(handler)
        assert len(events) == 1
        assert events[0] == {"type": "tool_end", "success": True}


# ===========================================================================
# SSEOutputHandler.pretty_print_json - Arguments
# ===========================================================================


class TestPrettyPrintJsonArguments:
    """Tests for SSEOutputHandler.pretty_print_json with title='Arguments'."""

    def test_emits_tool_args_event(self, handler):
        handler._last_tool_name = "search_file"
        args = {"query": "main", "directory": "/src"}
        handler.pretty_print_json(args, title="Arguments")
        events = _drain(handler)
        assert len(events) == 1
        event = events[0]
        assert event["type"] == "tool_args"
        assert event["tool"] == "search_file"
        assert event["args"] == args
        assert "query" in event["detail"]

    def test_returns_early_for_arguments_title(self, handler):
        """Arguments title should emit tool_args, not tool_result."""
        handler._last_tool_name = "test_tool"
        handler.pretty_print_json({"key": "val"}, title="Arguments")
        events = _drain(handler)
        assert len(events) == 1
        assert events[0]["type"] == "tool_args"

    def test_non_dict_data_with_arguments_title_emits_tool_result(self, handler):
        """If data is not a dict, even with title='Arguments', fall through."""
        handler.pretty_print_json("just a string", title="Arguments")
        events = _drain(handler)
        assert len(events) == 1
        assert events[0]["type"] == "tool_result"


# ===========================================================================
# SSEOutputHandler.pretty_print_json - Tool Results
# ===========================================================================


class TestPrettyPrintJsonToolResults:
    """Tests for SSEOutputHandler.pretty_print_json with various result types."""

    def test_basic_tool_result(self, handler):
        data = {"status": "success", "message": "Done"}
        handler.pretty_print_json(data, title="Result")
        events = _drain(handler)
        assert len(events) == 1
        event = events[0]
        assert event["type"] == "tool_result"
        assert event["title"] == "Result"
        assert event["success"] is True

    def test_error_status_marks_success_false(self, handler):
        data = {"status": "error", "message": "File not found"}
        handler.pretty_print_json(data, title="Result")
        events = _drain(handler)
        assert events[0]["success"] is False

    def test_non_dict_data_success_is_true(self, handler):
        handler.pretty_print_json("some string", title="Result")
        events = _drain(handler)
        assert events[0]["success"] is True

    def test_command_output_included(self, handler):
        data = {
            "command": "ls -la",
            "stdout": "file1.txt\nfile2.txt",
            "stderr": "",
            "return_code": 0,
            "cwd": "/home/user",
            "duration_seconds": 0.5,
            "output_truncated": False,
        }
        handler.pretty_print_json(data, title="Result")
        events = _drain(handler)
        event = events[0]
        assert "command_output" in event
        co = event["command_output"]
        assert co["command"] == "ls -la"
        assert co["stdout"] == "file1.txt\nfile2.txt"
        assert co["return_code"] == 0
        assert co["cwd"] == "/home/user"
        assert co["duration_seconds"] == 0.5
        assert co["truncated"] is False

    def test_command_output_with_stderr_only(self, handler):
        data = {
            "command": "bad_cmd",
            "stderr": "command not found",
            "return_code": 127,
        }
        handler.pretty_print_json(data, title="Result")
        events = _drain(handler)
        assert "command_output" in events[0]
        co = events[0]["command_output"]
        assert co["stderr"] == "command not found"
        assert co["return_code"] == 127

    def test_file_list_result_data(self, handler):
        files = ["file1.txt", "file2.txt", "file3.txt"]
        data = {"files": files, "count": 3}
        handler.pretty_print_json(data, title="Result")
        events = _drain(handler)
        event = events[0]
        assert "result_data" in event
        rd = event["result_data"]
        assert rd["type"] == "file_list"
        assert rd["files"] == files
        assert rd["total"] == 3

    def test_file_list_limited_to_20(self, handler):
        files = [f"file{i}.txt" for i in range(30)]
        data = {"files": files, "count": 30}
        handler.pretty_print_json(data, title="Result")
        events = _drain(handler)
        rd = events[0]["result_data"]
        assert len(rd["files"]) == 20
        assert rd["total"] == 30

    def test_file_list_via_file_list_key(self, handler):
        files = ["a.txt", "b.txt"]
        data = {"file_list": files, "count": 2}
        handler.pretty_print_json(data, title="Result")
        events = _drain(handler)
        rd = events[0]["result_data"]
        assert rd["files"] == files

    def test_chunks_result_data(self, handler):
        chunks = ["chunk1 text", "chunk2 text", "chunk3 text"]
        data = {"chunks": chunks, "source_files": ["doc.pdf"]}
        handler.pretty_print_json(data, title="Result")
        events = _drain(handler)
        event = events[0]
        assert "result_data" in event
        rd = event["result_data"]
        assert rd["type"] == "search_results"
        assert rd["count"] == 3
        assert rd["source_files"] == ["doc.pdf"]
        assert len(rd["chunks"]) == 3
        # Each string chunk is wrapped in a structured object with preview/content
        assert "preview" in rd["chunks"][0]

    def test_chunks_previews_truncated_to_150_chars(self, handler):
        long_chunk = "x" * 300
        data = {"chunks": [long_chunk]}
        handler.pretty_print_json(data, title="Result")
        events = _drain(handler)
        rd = events[0]["result_data"]
        # String chunks get a preview truncated to 150 chars
        assert len(rd["chunks"][0]["preview"]) == 150

    def test_chunks_limited_to_8(self, handler):
        chunks = [f"chunk{i}" for i in range(15)]
        data = {"chunks": chunks}
        handler.pretty_print_json(data, title="Result")
        events = _drain(handler)
        rd = events[0]["result_data"]
        # Count reflects total chunks, but structured list limited to 8
        assert rd["count"] == 15
        assert len(rd["chunks"]) == 8

    def test_no_title(self, handler):
        handler.pretty_print_json({"key": "val"})
        events = _drain(handler)
        assert events[0]["title"] is None


# ===========================================================================
# SSEOutputHandler.print_error
# ===========================================================================


class TestPrintError:
    """Tests for SSEOutputHandler.print_error."""

    def test_emits_agent_error_with_message(self, handler):
        handler.print_error("Something went wrong")
        events = _drain(handler)
        assert len(events) == 1
        assert events[0] == {
            "type": "agent_error",
            "content": "Something went wrong",
        }

    def test_emits_unknown_error_when_message_is_none(self, handler):
        handler.print_error(None)
        events = _drain(handler)
        assert events[0]["content"] == "Unknown error"

    def test_emits_unknown_error_when_message_is_empty(self, handler):
        handler.print_error("")
        events = _drain(handler)
        assert events[0]["content"] == "Unknown error"

    def test_non_string_error_is_converted(self, handler):
        handler.print_error(ValueError("bad value"))
        events = _drain(handler)
        assert "bad value" in events[0]["content"]


# ===========================================================================
# SSEOutputHandler.print_warning
# ===========================================================================


class TestPrintWarning:
    """Tests for SSEOutputHandler.print_warning."""

    def test_emits_warning_status(self, handler):
        handler.print_warning("Low disk space")
        events = _drain(handler)
        assert len(events) == 1
        assert events[0] == {
            "type": "status",
            "status": "warning",
            "message": "Low disk space",
        }


# ===========================================================================
# SSEOutputHandler.print_info
# ===========================================================================


class TestPrintInfo:
    """Tests for SSEOutputHandler.print_info."""

    def test_emits_info_status(self, handler):
        handler.print_info("Model loaded")
        events = _drain(handler)
        assert len(events) == 1
        assert events[0] == {
            "type": "status",
            "status": "info",
            "message": "Model loaded",
        }


# ===========================================================================
# SSEOutputHandler.start_progress
# ===========================================================================


class TestStartProgress:
    """Tests for SSEOutputHandler.start_progress."""

    def test_emits_thinking_for_normal_message(self, handler):
        handler.start_progress("Analyzing code...")
        events = _drain(handler)
        assert len(events) == 1
        assert events[0] == {"type": "thinking", "content": "Analyzing code..."}

    def test_filters_executing_prefix(self, handler):
        handler.start_progress("Executing search_file")
        events = _drain(handler)
        assert len(events) == 0

    def test_filters_executing_prefix_case_insensitive(self, handler):
        handler.start_progress("executing TOOL_NAME")
        events = _drain(handler)
        assert len(events) == 0

    def test_none_message_emits_working_fallback(self, handler):
        # None is falsy, so the startswith check is skipped; "message or 'Working'" applies
        handler.start_progress(None)
        events = _drain(handler)
        assert len(events) == 1
        assert events[0]["content"] == "Working"

    def test_empty_string_emits_working_fallback(self, handler):
        # "" is falsy, so startswith check skipped; "message or 'Working'" applies
        handler.start_progress("")
        events = _drain(handler)
        assert len(events) == 1
        assert events[0]["content"] == "Working"


# ===========================================================================
# SSEOutputHandler.stop_progress
# ===========================================================================


class TestStopProgress:
    """Tests for SSEOutputHandler.stop_progress."""

    def test_is_noop(self, handler):
        handler.stop_progress()
        events = _drain(handler)
        assert len(events) == 0


# ===========================================================================
# SSEOutputHandler.print_final_answer
# ===========================================================================


class TestPrintFinalAnswer:
    """Tests for SSEOutputHandler.print_final_answer."""

    def test_emits_answer_event(self, handler):
        handler._start_time = time.time() - 2.0
        handler._step_count = 3
        handler._tool_count = 5
        handler.print_final_answer("Here is the answer")
        events = _drain(handler)
        assert len(events) == 1
        event = events[0]
        assert event["type"] == "answer"
        assert event["content"] == "Here is the answer"
        assert event["steps"] == 3
        assert event["tools_used"] == 5
        assert event["elapsed"] >= 1.9

    def test_applies_fix_double_escaped(self, handler):
        # Create text with many literal \\n sequences (more than 2, and more
        # than 2x the real newlines which is 0)
        text = "line1\\nline2\\nline3\\nline4"
        handler.print_final_answer(text)
        events = _drain(handler)
        assert events[0]["content"] == "line1\nline2\nline3\nline4"

    def test_none_answer_passes_through(self, handler):
        handler.print_final_answer(None)
        events = _drain(handler)
        assert events[0]["content"] is None

    def test_empty_answer_not_fixed(self, handler):
        handler.print_final_answer("")
        events = _drain(handler)
        # Empty string is falsy, so _fix_double_escaped is not called
        assert events[0]["content"] == ""

    def test_elapsed_is_zero_without_start(self, handler):
        handler.print_final_answer("answer")
        events = _drain(handler)
        assert events[0]["elapsed"] == 0.0


# ===========================================================================
# SSEOutputHandler.print_repeated_tool_warning
# ===========================================================================


class TestPrintRepeatedToolWarning:
    """Tests for SSEOutputHandler.print_repeated_tool_warning."""

    def test_emits_correct_warning(self, handler):
        handler.print_repeated_tool_warning()
        events = _drain(handler)
        assert len(events) == 1
        assert events[0] == {
            "type": "status",
            "status": "warning",
            "message": "Detected repetitive tool call pattern. Execution paused.",
        }


# ===========================================================================
# SSEOutputHandler.print_completion
# ===========================================================================


class TestPrintCompletion:
    """Tests for SSEOutputHandler.print_completion."""

    def test_emits_complete_status(self, handler):
        handler._start_time = time.time() - 1.0
        handler.print_completion(steps_taken=5, steps_limit=10)
        events = _drain(handler)
        assert len(events) == 1
        event = events[0]
        assert event["type"] == "status"
        assert event["status"] == "complete"
        assert event["message"] == "Completed in 5 steps"
        assert event["steps"] == 5
        assert event["elapsed"] >= 0.9


# ===========================================================================
# SSEOutputHandler.print_step_paused
# ===========================================================================


class TestPrintStepPaused:
    """Tests for SSEOutputHandler.print_step_paused."""

    def test_is_noop(self, handler):
        handler.print_step_paused("Pausing for user input")
        events = _drain(handler)
        assert len(events) == 0


# ===========================================================================
# SSEOutputHandler.print_command_executing
# ===========================================================================


class TestPrintCommandExecuting:
    """Tests for SSEOutputHandler.print_command_executing."""

    def test_emits_tool_start_with_detail(self, handler):
        handler.print_command_executing("git status")
        events = _drain(handler)
        assert len(events) == 1
        assert events[0] == {
            "type": "tool_start",
            "tool": "run_shell_command",
            "detail": "git status",
        }


# ===========================================================================
# SSEOutputHandler.print_agent_selected
# ===========================================================================


class TestPrintAgentSelected:
    """Tests for SSEOutputHandler.print_agent_selected."""

    def test_emits_status_info(self, handler):
        handler.print_agent_selected("CodeAgent", "python", "web")
        events = _drain(handler)
        assert len(events) == 1
        assert events[0] == {
            "type": "status",
            "status": "info",
            "message": "Agent: CodeAgent",
        }


# ===========================================================================
# SSEOutputHandler.print_streaming_text
# ===========================================================================


class TestPrintStreamingText:
    """Tests for SSEOutputHandler.print_streaming_text."""

    def test_normal_text_emits_chunk(self, handler):
        handler.print_streaming_text("Hello, world!")
        events = _drain(handler)
        assert len(events) == 1
        assert events[0] == {"type": "chunk", "content": "Hello, world!"}

    def test_empty_text_no_event(self, handler):
        handler.print_streaming_text("")
        events = _drain(handler)
        assert len(events) == 0

    def test_none_text_no_event(self, handler):
        handler.print_streaming_text(None)
        events = _drain(handler)
        assert len(events) == 0

    def test_pure_tool_call_json_filtered(self, handler):
        """Complete tool-call JSON should be silently filtered."""
        tool_json = '{"tool": "search_file", "tool_args": {"query": "test"}}'
        handler.print_streaming_text(tool_json)
        events = _drain(handler)
        assert len(events) == 0
        assert handler._stream_buffer == ""

    def test_incomplete_tool_json_buffered(self, handler):
        """Incomplete tool JSON should stay in the buffer."""
        partial = '{"tool": "search_file", "tool_args": {"query":'
        handler.print_streaming_text(partial)
        events = _drain(handler)
        assert len(events) == 0
        assert handler._stream_buffer == partial

    def test_incomplete_then_complete_tool_json_filtered(self, handler):
        """When tool JSON arrives in two chunks, both should be filtered."""
        handler.print_streaming_text('{"tool": "search_file", "tool_args": {')
        handler.print_streaming_text('"query": "test"}}')
        events = _drain(handler)
        assert len(events) == 0
        assert handler._stream_buffer == ""

    def test_embedded_text_then_tool_json_split(self, handler):
        """Text followed by tool JSON should emit text and filter JSON."""
        mixed = 'I will search now.\n{"tool": "search_file", "tool_args": {"query": "test"}}'
        handler.print_streaming_text(mixed)
        events = _drain(handler)
        assert len(events) == 1
        assert events[0]["type"] == "chunk"
        assert "I will search now." in events[0]["content"]
        assert handler._stream_buffer == ""

    def test_buffer_overflow_emits_content(self, handler):
        """Buffer exceeding 2048 bytes should be flushed."""
        # Build a buffer that starts with { and contains "tool" but is huge
        large_text = '{"tool": "x"' + " " * 2100
        handler.print_streaming_text(large_text)
        events = _drain(handler)
        assert len(events) == 1
        assert events[0]["type"] == "chunk"
        assert handler._stream_buffer == ""

    def test_json_like_but_not_tool_call_emitted(self, handler):
        """JSON that has "tool" keyword but is not valid tool-call format."""
        not_tool = '{"tool": "search", "other_key": "not tool_args"}'
        handler.print_streaming_text(not_tool)
        events = _drain(handler)
        # Starts with { and has "tool", ends with }, but doesn't match regex
        assert len(events) == 1
        assert events[0]["type"] == "chunk"

    def test_end_of_stream_flushes_buffer_normal_text(self, handler):
        """end_of_stream=True with normal text in buffer should flush."""
        handler._stream_buffer = "leftover text"
        handler.print_streaming_text("", end_of_stream=True)
        events = _drain(handler)
        assert len(events) == 1
        assert events[0] == {"type": "chunk", "content": "leftover text"}
        assert handler._stream_buffer == ""

    def test_end_of_stream_filters_tool_json_in_buffer(self, handler):
        """end_of_stream=True should still filter tool-call JSON from buffer."""
        handler._stream_buffer = (
            '{"tool": "search_file", "tool_args": {"query": "test"}}'
        )
        handler.print_streaming_text("", end_of_stream=True)
        events = _drain(handler)
        assert len(events) == 0
        assert handler._stream_buffer == ""

    def test_end_of_stream_no_buffer_no_event(self, handler):
        """end_of_stream=True with empty buffer should not emit."""
        handler.print_streaming_text("", end_of_stream=True)
        events = _drain(handler)
        assert len(events) == 0

    def test_text_plus_incomplete_json_across_chunks(self, handler):
        """Normal text followed by partial JSON in a later chunk."""
        handler.print_streaming_text("Hello there.")
        events1 = _drain(handler)
        assert len(events1) == 1
        assert events1[0]["content"] == "Hello there."

        # Now partial tool JSON
        handler.print_streaming_text('{"tool": "read_file", "tool_args": {')
        events2 = _drain(handler)
        # Should be buffering
        assert len(events2) == 0

        # Complete it
        handler.print_streaming_text('"path": "/a.txt"}}')
        events3 = _drain(handler)
        # Should be filtered
        assert len(events3) == 0
        assert handler._stream_buffer == ""

    def test_lone_brace_buffered_until_pattern_detected(self, handler):
        """A lone '{' should be buffered (Case 0) until a marker appears."""
        handler.print_streaming_text("{")
        events = _drain(handler)
        # Should NOT be emitted yet — waiting for more tokens
        assert len(events) == 0
        assert handler._stream_buffer == "{"

    def test_answer_json_token_by_token_filtered(self, handler):
        """Token-by-token {"answer": "..."} should be filtered, not emitted."""
        handler.print_streaming_text("{")
        handler.print_streaming_text('"answer": "Hello world"}')
        events = _drain(handler)
        # The answer JSON should be filtered entirely
        assert len(events) == 0
        assert handler._stream_buffer == ""

    def test_brace_followed_by_non_json_emits(self, handler):
        """A '{' followed by normal text (no markers) should be flushed."""
        handler.print_streaming_text("{")
        events1 = _drain(handler)
        assert len(events1) == 0  # Still buffered

        # More text that makes buffer > 30 chars and isn't JSON-like
        handler.print_streaming_text(" this is not JSON, it's just curly-braced text")
        events2 = _drain(handler)
        # Now it should emit since it's clearly not LLM JSON
        assert len(events2) == 1
        assert "{" in events2[0]["content"]


# ===========================================================================
# _clean_answer_json
# ===========================================================================


class TestCleanAnswerJson:
    """Tests for the _clean_answer_json helper function."""

    def test_valid_json_answer_extracted(self):
        from gaia.ui.sse_handler import _clean_answer_json

        text = '{"answer": "Hello, world!"}'
        assert _clean_answer_json(text) == "Hello, world!"

    def test_json_with_escaped_newlines(self):
        from gaia.ui.sse_handler import _clean_answer_json

        text = '{"answer": "Line 1\\nLine 2\\nLine 3"}'
        result = _clean_answer_json(text)
        assert result == "Line 1\nLine 2\nLine 3"

    def test_json_with_literal_newlines(self):
        """LLMs often emit literal newlines inside JSON strings (invalid JSON)."""
        from gaia.ui.sse_handler import _clean_answer_json

        text = '{"answer": "Document Summary\n\nI\'d be happy to help.\n\n1. First\n2. Second"}'
        result = _clean_answer_json(text)
        assert "Document Summary" in result
        assert "I'd be happy to help." in result
        assert "1. First" in result

    def test_non_answer_json_returned_unchanged(self):
        from gaia.ui.sse_handler import _clean_answer_json

        text = '{"thought": "I should search"}'
        assert _clean_answer_json(text) == text

    def test_plain_text_returned_unchanged(self):
        from gaia.ui.sse_handler import _clean_answer_json

        text = "Just a normal response"
        assert _clean_answer_json(text) == text

    def test_empty_string(self):
        from gaia.ui.sse_handler import _clean_answer_json

        assert _clean_answer_json("") == ""

    def test_none_input(self):
        from gaia.ui.sse_handler import _clean_answer_json

        assert _clean_answer_json(None) is None

    def test_answer_with_markdown(self):
        """Verify markdown content is preserved."""
        from gaia.ui.sse_handler import _clean_answer_json

        text = (
            '{"answer": "# Title\n\n**Bold text** and *italic*\n\n1. First\n2. Second"}'
        )
        result = _clean_answer_json(text)
        assert "# Title" in result
        assert "**Bold text**" in result


# ===========================================================================
# SSEOutputHandler.signal_done
# ===========================================================================


class TestSignalDone:
    """Tests for SSEOutputHandler.signal_done."""

    def test_emits_sentinel_none(self, handler):
        handler.signal_done()
        events = _drain(handler)
        assert len(events) == 1
        assert events[0] is None

    def test_flushes_normal_text_buffer(self, handler):
        handler._stream_buffer = "remaining text"
        handler.signal_done()
        events = _drain(handler)
        assert len(events) == 2
        assert events[0] == {"type": "chunk", "content": "remaining text"}
        assert events[1] is None

    def test_filters_tool_json_buffer_on_done(self, handler):
        handler._stream_buffer = '{"tool": "search_file", "tool_args": {"query": "x"}}'
        handler.signal_done()
        events = _drain(handler)
        # Tool JSON filtered, only sentinel emitted
        assert len(events) == 1
        assert events[0] is None
        assert handler._stream_buffer == ""

    def test_clears_buffer_after_done(self, handler):
        handler._stream_buffer = "some text"
        handler.signal_done()
        assert handler._stream_buffer == ""


# ===========================================================================
# _format_tool_args
# ===========================================================================


class TestFormatToolArgs:
    """Tests for the _format_tool_args helper function."""

    def test_empty_args_returns_empty_string(self):
        assert _format_tool_args("tool", {}) == ""

    def test_none_args_returns_empty_string(self):
        assert _format_tool_args("tool", None) == ""

    def test_skips_none_values(self):
        result = _format_tool_args("tool", {"a": "hello", "b": None})
        assert "a: hello" in result
        assert "b" not in result

    def test_skips_empty_string_values(self):
        result = _format_tool_args("tool", {"a": "hello", "b": ""})
        assert "a: hello" in result
        assert "b" not in result

    def test_skips_false_values(self):
        result = _format_tool_args("tool", {"a": "hello", "b": False})
        assert "a: hello" in result
        assert "b" not in result

    def test_true_values_show_key_only(self):
        result = _format_tool_args("tool", {"recursive": True})
        assert result == "recursive"

    def test_long_strings_truncated_at_150(self):
        long_val = "x" * 200
        result = _format_tool_args("tool", {"content": long_val})
        assert "content: " in result
        assert result.endswith("...")
        # 150 chars + "content: " prefix + "..."
        assert "x" * 150 in result
        assert "x" * 151 not in result

    def test_two_or_fewer_parts_comma_joined(self):
        result = _format_tool_args("tool", {"a": "1", "b": "2"})
        assert result == "a: 1, b: 2"

    def test_more_than_two_parts_newline_joined(self):
        result = _format_tool_args("tool", {"a": "1", "b": "2", "c": "3"})
        assert result == "a: 1\nb: 2\nc: 3"

    def test_numeric_values(self):
        result = _format_tool_args("tool", {"count": 42})
        assert "count: 42" in result

    def test_mixed_skippable_and_valid(self):
        result = _format_tool_args(
            "tool", {"a": "hello", "b": None, "c": False, "d": "", "e": True}
        )
        # Only "a" and "e" should be present (2 parts -> comma-joined)
        assert result == "a: hello, e"


# ===========================================================================
# _summarize_tool_result
# ===========================================================================


class TestSummarizeToolResult:
    """Tests for the _summarize_tool_result helper function."""

    # --- Non-dict data ---

    def test_non_dict_returns_truncated_string(self):
        result = _summarize_tool_result("simple string")
        assert result == "simple string"

    def test_non_dict_long_string_truncated_to_300(self):
        long_str = "a" * 500
        result = _summarize_tool_result(long_str)
        assert len(result) == 300

    def test_non_dict_list(self):
        result = _summarize_tool_result([1, 2, 3])
        assert "1" in result

    # --- Command execution results ---

    def test_command_success_with_output(self):
        data = {
            "command": "ls",
            "stdout": "file1.txt\nfile2.txt\nfile3.txt",
            "return_code": 0,
        }
        result = _summarize_tool_result(data)
        assert "file1.txt" in result
        assert "file2.txt" in result

    def test_command_success_no_output(self):
        data = {"command": "mkdir test", "stdout": "", "return_code": 0}
        result = _summarize_tool_result(data)
        assert result == "Command completed (no output)"

    def test_command_success_whitespace_only_stdout(self):
        data = {"command": "echo", "stdout": "   \n  \n  ", "return_code": 0}
        result = _summarize_tool_result(data)
        assert result == "Command completed (no output)"

    def test_command_failure(self):
        data = {
            "command": "bad_cmd",
            "stdout": "",
            "stderr": "command not found",
            "return_code": 127,
        }
        result = _summarize_tool_result(data)
        assert "Command failed (exit 127)" in result
        assert "command not found" in result

    def test_command_failure_no_stderr(self):
        data = {"command": "bad_cmd", "stdout": "", "stderr": "", "return_code": 1}
        result = _summarize_tool_result(data)
        assert result == "Command failed (exit 1)"

    def test_command_output_truncated_at_5_lines(self):
        stdout = "\n".join(f"line{i}" for i in range(10))
        data = {"command": "cat big.txt", "stdout": stdout, "return_code": 0}
        result = _summarize_tool_result(data)
        assert "line0" in result
        assert "line4" in result
        assert "10 lines total" in result

    def test_command_output_exactly_5_lines(self):
        stdout = "\n".join(f"line{i}" for i in range(5))
        data = {"command": "cat file.txt", "stdout": stdout, "return_code": 0}
        result = _summarize_tool_result(data)
        assert "line0" in result
        assert "line4" in result
        assert "lines total" not in result

    def test_command_stderr_truncated_at_150(self):
        stderr = "e" * 200
        data = {
            "command": "cmd",
            "stdout": "",
            "stderr": stderr,
            "return_code": 1,
        }
        result = _summarize_tool_result(data)
        assert len(result.split(": ", 1)[1]) == 150

    # --- File search results ---

    def test_file_list_with_files(self):
        data = {"files": ["a.txt", "b.txt", "c.txt"], "count": 3}
        result = _summarize_tool_result(data)
        assert "Found 3 file(s)" in result
        assert "a.txt" in result

    def test_file_list_with_dict_files(self):
        data = {
            "files": [
                {"name": "test.py", "directory": "/src"},
                {"name": "main.py", "directory": "/app"},
            ],
            "count": 2,
        }
        result = _summarize_tool_result(data)
        assert "test.py (/src)" in result
        assert "main.py (/app)" in result

    def test_file_list_dict_without_directory(self):
        data = {"files": [{"name": "readme.md"}], "count": 1}
        result = _summarize_tool_result(data)
        assert "readme.md" in result

    def test_file_list_with_filename_key(self):
        data = {"files": [{"filename": "data.csv"}], "count": 1}
        result = _summarize_tool_result(data)
        assert "data.csv" in result

    def test_file_list_more_than_5(self):
        data = {"files": [f"f{i}.txt" for i in range(10)], "count": 10}
        result = _summarize_tool_result(data)
        assert "+5 more" in result

    def test_file_list_with_display_message(self):
        data = {
            "files": ["a.txt"],
            "count": 1,
            "display_message": "Search complete",
        }
        result = _summarize_tool_result(data)
        assert result.startswith("Search complete")
        assert "a.txt" in result

    def test_file_list_empty_with_display_message(self):
        data = {"files": [], "count": 0, "display_message": "No files matched"}
        result = _summarize_tool_result(data)
        assert result == "No files matched"

    def test_file_list_empty_no_display_message(self):
        data = {"files": [], "count": 0}
        result = _summarize_tool_result(data)
        assert result == "Found 0 file(s)"

    def test_file_list_via_file_list_key(self):
        data = {"file_list": ["x.txt", "y.txt"], "count": 2}
        result = _summarize_tool_result(data)
        assert "Found 2 file(s)" in result

    # --- Search/query results with chunks ---

    def test_chunks_basic(self):
        data = {"chunks": ["chunk1", "chunk2"]}
        result = _summarize_tool_result(data)
        assert "Found 2 relevant chunk(s)" in result

    def test_chunks_with_scores(self):
        data = {"chunks": ["c1", "c2"], "scores": [0.95, 0.80]}
        result = _summarize_tool_result(data)
        assert "best score: 0.95" in result

    def test_chunks_with_string_preview(self):
        data = {"chunks": ["This is relevant content about Python"]}
        result = _summarize_tool_result(data)
        assert 'Top match: "This is relevant content about Python' in result

    def test_chunks_preview_truncated_at_120(self):
        data = {"chunks": ["x" * 200]}
        result = _summarize_tool_result(data)
        # The preview is truncated to 120 chars
        assert '..."' in result

    def test_chunks_non_string_no_preview(self):
        data = {"chunks": [{"text": "content", "page": 1}]}
        result = _summarize_tool_result(data)
        assert "Found 1 relevant chunk(s)" in result
        assert "Top match" not in result

    def test_chunks_empty_list(self):
        data = {"chunks": []}
        result = _summarize_tool_result(data)
        assert "Found 0 relevant chunk(s)" in result

    # --- Generic results ---

    def test_results_list(self):
        data = {"results": [1, 2, 3]}
        result = _summarize_tool_result(data)
        assert result == "Found 3 result(s)"

    def test_results_non_list(self):
        data = {"results": "some text result"}
        result = _summarize_tool_result(data)
        assert result == "some text result"

    def test_results_non_list_truncated(self):
        data = {"results": "x" * 300}
        result = _summarize_tool_result(data)
        assert len(result) == 200

    # --- Document indexing ---

    def test_indexing_with_num_chunks_and_filename(self):
        data = {"num_chunks": 42, "filename": "report.pdf"}
        result = _summarize_tool_result(data)
        assert result == "Indexed report.pdf (42 chunks)"

    def test_indexing_with_chunk_count_and_file_path(self):
        data = {"chunk_count": 10, "file_path": "/docs/readme.md"}
        result = _summarize_tool_result(data)
        assert result == "Indexed /docs/readme.md (10 chunks)"

    def test_indexing_without_filename(self):
        data = {"num_chunks": 5}
        result = _summarize_tool_result(data)
        assert result == "Indexed document (5 chunks)"

    # --- File read results ---

    def test_file_read_result(self):
        data = {
            "content": "line1\nline2\nline3",
            "filepath": "/src/main.py",
            "filename": "main.py",
        }
        result = _summarize_tool_result(data)
        assert result == "Read 3 lines from main.py"

    def test_file_read_result_fallback_to_filepath(self):
        data = {"content": "single line", "filepath": "/src/main.py"}
        result = _summarize_tool_result(data)
        assert result == "Read 1 lines from /src/main.py"

    # --- Status-based results ---

    def test_status_with_message(self):
        data = {"status": "success", "message": "Operation completed"}
        result = _summarize_tool_result(data)
        assert result == "success: Operation completed"

    def test_status_with_error(self):
        data = {"status": "error", "error": "File not found"}
        result = _summarize_tool_result(data)
        assert result == "error: File not found"

    def test_status_with_display_message(self):
        data = {"status": "ok", "display_message": "All good"}
        result = _summarize_tool_result(data)
        assert result == "ok: All good"

    def test_status_without_message(self):
        data = {"status": "running"}
        result = _summarize_tool_result(data)
        assert result == "running"

    def test_status_message_truncated_at_200(self):
        data = {"status": "info", "message": "m" * 300}
        result = _summarize_tool_result(data)
        msg_part = result.split(": ", 1)[1]
        assert len(msg_part) == 200

    # --- Generic fallback ---

    def test_generic_fallback_shows_keys(self):
        data = {"alpha": 1, "beta": 2, "gamma": 3}
        result = _summarize_tool_result(data)
        assert result == "Result with keys: alpha, beta, gamma"

    def test_generic_fallback_limits_to_6_keys(self):
        data = {f"key{i}": i for i in range(10)}
        result = _summarize_tool_result(data)
        # Should only show first 6 keys
        keys_str = result.replace("Result with keys: ", "")
        keys = keys_str.split(", ")
        assert len(keys) == 6


# ===========================================================================
# _fix_double_escaped
# ===========================================================================


class TestFixDoubleEscaped:
    """Tests for the _fix_double_escaped helper function."""

    def test_none_returns_none(self):
        assert _fix_double_escaped(None) is None

    def test_empty_string_returns_empty(self):
        assert _fix_double_escaped("") == ""

    def test_no_escapes_unchanged(self):
        text = "Hello, world!\nThis is fine."
        assert _fix_double_escaped(text) == text

    def test_few_literal_escapes_unchanged(self):
        # Only 2 literal \\n, threshold is > 2
        text = "line1\\nline2\\nline3"
        assert _fix_double_escaped(text) == text

    def test_many_literal_escapes_fixed(self):
        # 3 literal \\n, 0 real newlines -> 3 > 0*2, and 3 > 2
        text = "line1\\nline2\\nline3\\nline4"
        assert _fix_double_escaped(text) == "line1\nline2\nline3\nline4"

    def test_tabs_also_fixed(self):
        text = "col1\\tcol2\\nrow2_col1\\trow2_col2\\nrow3\\n"
        # 3 literal \\n -> triggers fix; also fixes \\t
        result = _fix_double_escaped(text)
        assert "\t" in result
        assert "\\t" not in result

    def test_escaped_quotes_also_fixed(self):
        text = 'He said \\"hello\\"\\nShe said \\"bye\\"\\nEnd\\n'
        result = _fix_double_escaped(text)
        assert '"hello"' in result
        assert '\\"' not in result

    def test_mixed_real_and_literal_no_fix(self):
        # 3 literal \\n but 5 real \n -> 3 > 10 is false, so no fix
        text = "real\nnewlines\nare\nmore\ncommon\nthan\\nliteral\\nones\\n"
        assert _fix_double_escaped(text) == text

    def test_all_real_newlines_no_fix(self):
        text = "line1\nline2\nline3\n"
        assert _fix_double_escaped(text) == text

    def test_ratio_boundary_no_fix(self):
        # 3 literal, 2 real -> 3 > 4 is false
        text = "real\nnewline\nhere\\nand\\nliteral\\n"
        assert _fix_double_escaped(text) == text

    def test_ratio_boundary_triggers_fix(self):
        # 4 literal, 1 real -> 4 > 2 is true, and 4 > 2
        text = "real\nhere\\nand\\nliteral\\nmore\\n"
        result = _fix_double_escaped(text)
        assert "\\n" not in result


# ===========================================================================
# _tool_description
# ===========================================================================


class TestToolDescription:
    """Tests for the _tool_description helper function."""

    def test_known_tool_returns_description(self):
        assert (
            _tool_description("search_file") == "Searching for files matching a pattern"
        )

    def test_read_file(self):
        assert _tool_description("read_file") == "Reading file contents"

    def test_run_shell_command(self):
        assert _tool_description("run_shell_command") == "Executing a shell command"

    def test_search_documents(self):
        assert (
            _tool_description("search_documents")
            == "Searching indexed documents for relevant content"
        )

    def test_list_directory(self):
        assert _tool_description("list_directory") == "Listing directory contents"

    def test_write_file(self):
        assert _tool_description("write_file") == "Writing to a file"

    def test_create_file(self):
        assert _tool_description("create_file") == "Creating a new file"

    def test_get_file_preview(self):
        assert _tool_description("get_file_preview") == "Previewing file contents"

    def test_unknown_tool_returns_empty_string(self):
        assert _tool_description("totally_unknown_tool") == ""

    def test_empty_string_tool_returns_empty(self):
        assert _tool_description("") == ""


# ===========================================================================
# Integration-like tests: full event sequences
# ===========================================================================


class TestEventSequences:
    """Tests that verify realistic sequences of handler calls."""

    def test_typical_agent_lifecycle(self, handler):
        """Verify events from a typical agent processing cycle."""
        handler.print_processing_start("What is Python?", max_steps=5)
        handler.print_step_header(1, 5)
        handler.print_thought("I need to search for information")
        handler.print_tool_usage("search_file")
        handler.pretty_print_json({"query": "Python"}, title="Arguments")
        handler.pretty_print_json({"files": ["docs.txt"], "count": 1}, title="Result")
        handler.print_tool_complete()
        handler.print_final_answer("Python is a programming language.")
        handler.print_completion(1, 5)
        handler.signal_done()

        events = _drain(handler)

        # Verify event types in order
        # Note: print_processing_start no longer emits a thinking event
        # (it was suppressed to reduce noise since _chat_helpers emits its own)
        event_types = [e["type"] if e is not None else None for e in events]
        assert event_types == [
            "step",  # step_header
            "thinking",  # thought
            "tool_start",  # tool_usage
            "tool_args",  # pretty_print_json Arguments
            "tool_result",  # pretty_print_json Result
            "tool_end",  # tool_complete
            "answer",  # final_answer
            "status",  # completion
            None,  # signal_done sentinel
        ]

    def test_error_recovery_sequence(self, handler):
        """Verify events when an error occurs during processing."""
        handler.print_processing_start("Bad query", max_steps=3)
        handler.print_step_header(1, 3)
        handler.print_tool_usage("dangerous_tool")
        handler.print_error("Tool execution failed: timeout")
        handler.signal_done()

        events = _drain(handler)
        error_events = [e for e in events if e and e.get("type") == "agent_error"]
        assert len(error_events) == 1
        assert "timeout" in error_events[0]["content"]

    def test_streaming_with_signal_done(self, handler):
        """Verify streaming text properly flushed by signal_done."""
        handler.print_streaming_text("Hello ")
        handler.print_streaming_text("world!")
        handler.signal_done()

        events = _drain(handler)
        # Two chunk events + sentinel
        chunk_events = [e for e in events if e and e.get("type") == "chunk"]
        assert len(chunk_events) == 2
        combined = "".join(e["content"] for e in chunk_events)
        assert combined == "Hello world!"
        assert events[-1] is None
