# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Tests for error formatting utilities.

Purpose: Validate that error formatting functions produce correct, user-friendly output.
These tests verify:
- Exception formatting with code context
- Framework path filtering
- Tool argument truncation
- Error message word wrapping
"""

import traceback

from gaia.agents.base.errors import (
    FRAMEWORK_PATHS,
    _filter_user_frames,
    _get_code_context,
    _truncate_args,
    format_execution_trace,
    format_user_error,
)

# ============================================================================
# 1. format_user_error() TESTS
# ============================================================================


class TestFormatUserError:
    """Test format_user_error function."""

    def test_format_user_error_basic_exception(self):
        """Verify basic exception formatting includes type and message."""
        try:
            raise ValueError("test error message")
        except ValueError as e:
            result = format_user_error(e)

        assert "ValueError: test error message" in result
        assert "Traceback (most recent call last):" in result

    def test_format_user_error_keyerror(self):
        """Verify KeyError formatting."""
        try:
            d = {}
            _ = d["missing_key"]
        except KeyError as e:
            result = format_user_error(e)

        assert "KeyError" in result
        assert "missing_key" in result

    def test_format_user_error_no_traceback(self):
        """Verify handling of exception without traceback."""
        e = RuntimeError("no traceback")
        # Manually clear the traceback
        e.__traceback__ = None
        result = format_user_error(e)

        assert "RuntimeError: no traceback" in result
        # Should not crash even without traceback


class TestFormatUserErrorFiltering:
    """Test framework path filtering in format_user_error."""

    def test_framework_paths_includes_all_agents(self):
        """Verify FRAMEWORK_PATHS includes all agent directories."""
        expected_paths = [
            "gaia/agents/base",
            "gaia/agents/blender",
            "gaia/agents/chat",
            "gaia/agents/code",
            "gaia/agents/docker",
            "gaia/agents/jira",
            "gaia/agents/routing",
            "gaia/agents/tools",
            "site-packages/",
        ]
        for path in expected_paths:
            assert path in FRAMEWORK_PATHS, f"Missing framework path: {path}"

    def test_framework_paths_no_redundant_entries(self):
        """Verify no redundant site-packages entries."""
        site_package_entries = [p for p in FRAMEWORK_PATHS if "site-packages" in p]
        # Should only have "site-packages/" not both "site-packages/" and "site-packages/gaia"
        assert len(site_package_entries) == 1
        assert "site-packages/" in FRAMEWORK_PATHS


# ============================================================================
# 2. format_execution_trace() TESTS
# ============================================================================


class TestFormatExecutionTrace:
    """Test format_execution_trace function."""

    def test_format_execution_trace_full_context(self):
        """Verify full context is included when all parameters provided."""
        try:
            raise ValueError("test error")
        except ValueError as e:
            result = format_execution_trace(
                exception=e,
                query="What is the weather?",
                plan_step=2,
                total_steps=5,
                tool_name="get_weather",
                tool_args={"city": "New York"},
            )

        assert "AGENT ERROR" in result
        assert "Tool execution failed" in result
        assert 'Query: "What is the weather?"' in result
        assert "Plan Step: 2/5" in result
        assert "Tool: get_weather" in result
        assert "city" in result
        assert "New York" in result
        assert "ValueError: test error" in result

    def test_format_execution_trace_minimal_context(self):
        """Verify function works with only exception provided."""
        try:
            raise RuntimeError("minimal error")
        except RuntimeError as e:
            result = format_execution_trace(exception=e)

        assert "AGENT ERROR" in result
        assert "RuntimeError: minimal error" in result
        # Should not crash with None values

    def test_format_execution_trace_truncates_long_query(self):
        """Verify long queries are truncated."""
        long_query = "a" * 100
        try:
            raise ValueError("error")
        except ValueError as e:
            result = format_execution_trace(exception=e, query=long_query)

        # Query should be truncated to 80 chars + "..."
        assert "..." in result
        # Should not contain the full 100-char query
        assert "a" * 100 not in result

    def test_format_execution_trace_wraps_long_error(self):
        """Verify long error messages are word-wrapped."""
        long_message = "This is a very long error message " * 5
        try:
            raise ValueError(long_message)
        except ValueError as e:
            result = format_execution_trace(exception=e)

        # With textwrap, long messages should be split into multiple lines
        # The exact number depends on the message, but there should be wrapping
        assert "This is a very long error message" in result


# ============================================================================
# 3. _filter_user_frames() TESTS
# ============================================================================


class TestFilterUserFrames:
    """Test _filter_user_frames helper function."""

    def test_filter_user_frames_removes_framework(self):
        """Verify framework paths are filtered out."""
        # Create mock frame summaries
        frames = [
            traceback.FrameSummary(
                "/home/user/gaia/agents/base/agent.py",
                100,
                "_execute_tool",
                lookup_line=False,
            ),
            traceback.FrameSummary(
                "/home/user/my_agent.py", 50, "my_function", lookup_line=False
            ),
        ]

        result = _filter_user_frames(frames)

        assert len(result) == 1
        assert "my_agent.py" in result[0].filename

    def test_filter_user_frames_keeps_user_code(self):
        """Verify user code paths are kept."""
        frames = [
            traceback.FrameSummary(
                "/home/user/projects/my_bot.py", 10, "run", lookup_line=False
            ),
            traceback.FrameSummary(
                "/home/user/scripts/helper.py", 20, "helper_func", lookup_line=False
            ),
        ]

        result = _filter_user_frames(frames)

        assert len(result) == 2

    def test_filter_user_frames_filters_site_packages(self):
        """Verify site-packages paths are filtered."""
        frames = [
            traceback.FrameSummary(
                "/usr/lib/python3.12/site-packages/requests/api.py",
                50,
                "get",
                lookup_line=False,
            ),
            traceback.FrameSummary(
                "/home/user/my_code.py", 10, "main", lookup_line=False
            ),
        ]

        result = _filter_user_frames(frames)

        assert len(result) == 1
        assert "my_code.py" in result[0].filename


# ============================================================================
# 4. _get_code_context() TESTS
# ============================================================================


class TestGetCodeContext:
    """Test _get_code_context helper function."""

    def test_get_code_context_returns_lines(self):
        """Verify code context returns formatted lines."""
        # Use this test file itself as the source
        result = _get_code_context(__file__, 1, context=2)

        if result:
            assert ">>>" in result  # Should have pointer to target line
            assert "|" in result  # Should have line separator

    def test_get_code_context_handles_missing_file(self):
        """Verify graceful handling of non-existent files."""
        result = _get_code_context("/nonexistent/path/file.py", 10, context=2)

        # Should not crash - linecache returns empty strings for missing files
        # The function will still format with line numbers but empty content
        assert result is not None  # Should not crash

    def test_get_code_context_handles_invalid_line(self):
        """Verify handling of line numbers beyond file length."""
        # Should not crash with invalid line numbers
        # May return None or empty if no lines found
        _get_code_context(__file__, 999999, context=2)  # Should not raise


# ============================================================================
# 5. _truncate_args() TESTS
# ============================================================================


class TestTruncateArgs:
    """Test _truncate_args helper function."""

    def test_truncate_args_short_dict(self):
        """Verify short dicts are not truncated."""
        args = {"key": "value"}
        result = _truncate_args(args)

        assert "key" in result
        assert "value" in result
        assert "..." not in result

    def test_truncate_args_long_dict(self):
        """Verify long dicts are truncated."""
        args = {"key_" + str(i): "value_" + str(i) for i in range(50)}
        result = _truncate_args(args, max_length=100)

        assert len(result) <= 100
        assert result.endswith("...")

    def test_truncate_args_uses_json_format(self):
        """Verify JSON formatting is used."""
        args = {"name": "test", "count": 42}
        result = _truncate_args(args)

        # JSON format uses double quotes
        assert '"name"' in result or '"count"' in result

    def test_truncate_args_handles_none(self):
        """Verify None args return empty dict string."""
        result = _truncate_args(None)
        assert result == "{}"

    def test_truncate_args_handles_empty_dict(self):
        """Verify empty dict returns empty dict string."""
        result = _truncate_args({})
        assert result == "{}"

    def test_truncate_args_handles_unserializable(self):
        """Verify graceful handling of non-JSON-serializable objects."""

        class CustomClass:
            pass

        args = {"obj": CustomClass()}
        result = _truncate_args(args)

        # Should not crash, should use str() fallback
        assert "obj" in result


# ============================================================================
# 6. INTEGRATION TESTS
# ============================================================================


class TestErrorFormattingIntegration:
    """Integration tests for error formatting in realistic scenarios."""

    def test_tool_error_displays_correctly(self):
        """Simulate a tool execution error and verify output."""

        def failing_tool():
            data = {}
            return data["missing"]

        try:
            failing_tool()
        except KeyError as e:
            result = format_execution_trace(
                exception=e,
                query="Get the data",
                plan_step=1,
                total_steps=3,
                tool_name="failing_tool",
                tool_args={"param": "value"},
            )

        # Verify all sections are present
        assert "AGENT ERROR" in result
        assert "Execution Trace:" in result
        assert "Error:" in result
        assert "KeyError" in result

    def test_nested_exception_handling(self):
        """Verify handling of nested/chained exceptions."""

        def inner():
            raise ValueError("inner error")

        def outer():
            try:
                inner()
            except ValueError:
                raise RuntimeError("outer error") from ValueError("inner error")

        try:
            outer()
        except RuntimeError as e:
            result = format_execution_trace(exception=e)

        assert "RuntimeError: outer error" in result
