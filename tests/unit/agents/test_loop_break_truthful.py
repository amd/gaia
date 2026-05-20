# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Tests for ``Agent._build_loop_break_summary``.

Regression coverage for the lie-on-loop bug: when the agent gets stuck
calling a non-existent tool repeatedly, ``max_consecutive_repeats``
forced the framework to return ``"Task completed with <bad-name>."`` —
a silent lie. The new helper branches on whether the last result was an
error and surfaces the actual failure instead.

These tests exercise the helper directly so they're deterministic — no
live Lemonade, no LLM sampling. They run in milliseconds.
"""

from unittest.mock import patch

import pytest

from gaia.agents.base.agent import Agent


class _DummyAgent(Agent):
    """Minimal concrete Agent — copied pattern from test_response_format."""

    def _get_system_prompt(self) -> str:
        return "test"

    def _register_tools(self) -> None:
        pass

    def _create_console(self):
        from gaia.agents.base.console import AgentConsole

        return AgentConsole()


@pytest.fixture
def agent():
    with patch("gaia.agents.base.agent.AgentSDK"):
        return _DummyAgent(silent_mode=True, skip_lemonade=True)


# ---------------------------------------------------------------------------
# Error path: must NOT claim success
# ---------------------------------------------------------------------------


def test_loop_break_on_repeated_error_does_not_claim_success(agent):
    summary = agent._build_loop_break_summary(
        tool_name="mcp_tool_mcp",
        consecutive_count=4,
        step_results=[
            {"status": "error", "error": "Tool 'mcp_tool_mcp' not found"},
        ],
    )
    assert "Task completed" not in summary
    assert "kept failing" in summary


def test_loop_break_on_error_surfaces_underlying_message(agent):
    summary = agent._build_loop_break_summary(
        tool_name="some_tool",
        consecutive_count=4,
        step_results=[
            {"status": "error", "error": "Connection refused"},
        ],
    )
    assert "Connection refused" in summary


def test_loop_break_on_error_with_empty_message_falls_back(agent):
    """Missing/empty ``error`` field doesn't break the helper."""
    summary = agent._build_loop_break_summary(
        tool_name="some_tool",
        consecutive_count=4,
        step_results=[{"status": "error"}],
    )
    assert "kept failing" in summary
    assert "the tool returned an error" in summary


def test_loop_break_on_other_error_shapes(agent):
    """``_is_error_result`` accepts multiple shapes; helper must too."""
    # success=False shape
    s1 = agent._build_loop_break_summary(
        tool_name="x",
        consecutive_count=4,
        step_results=[{"success": False, "error": "boom"}],
    )
    assert "Task completed" not in s1
    # has_errors shape
    s2 = agent._build_loop_break_summary(
        tool_name="x",
        consecutive_count=4,
        step_results=[{"has_errors": True, "error": "boom"}],
    )
    assert "Task completed" not in s2


# ---------------------------------------------------------------------------
# Success path: existing behaviour preserved
# ---------------------------------------------------------------------------


def test_loop_break_on_success_returns_task_completed_message(agent):
    """Loop-on-success (e.g. model keeps re-calling a working tool) should
    still produce the existing "Task completed" wording — no behaviour
    change on the happy path."""
    summary = agent._build_loop_break_summary(
        tool_name="query_documents",
        consecutive_count=4,
        step_results=[{"status": "success", "result": "found 3 docs"}],
    )
    assert summary.startswith("Task completed with query_documents")


def test_loop_break_with_empty_step_results_returns_task_completed(agent):
    """Edge case: no recorded results yet. Default to the success
    message (the loop wouldn't fire without prior calls in practice)."""
    summary = agent._build_loop_break_summary(
        tool_name="x", consecutive_count=4, step_results=[]
    )
    assert "Task completed" in summary


# ---------------------------------------------------------------------------
# Native-path call-site regression: the helper must be called with the
# UNWRAPPED tool-result list, not a wrapper list.
#
# Phase 4 originally passed ``step_results`` at both call sites. The legacy
# single-tool path was right — it appends to ``step_results``. But the
# NATIVE path appends to ``previous_outputs`` (each entry is
# ``{"tool": ..., "args": ..., "result": ...}``). Passing ``step_results``
# at the native site got the helper an empty list, the error branch
# never fired, and the agent emitted ``"Task completed with <bad-name>"``
# after a loop of failures. The fix unwraps ``previous_outputs`` at the
# call site. This test asserts the helper works correctly when fed
# unwrapped results — which is what the call site must produce.
# ---------------------------------------------------------------------------


def test_helper_handles_native_path_unwrapped_results(agent):
    """The fix at the native call site is::

        recent_results = [o.get("result") for o in previous_outputs]
        final_answer = self._build_loop_break_summary(
            tool_name, consecutive_count, recent_results
        )

    Assert the helper correctly identifies a loop-of-errors when fed
    the unwrapped results — i.e., it surfaces the underlying error
    instead of returning ``"Task completed with ..."``.
    """
    # Simulate three identical failed calls to a bad tool name. After the
    # fourth call (which would fire the loop-break), the result list
    # contains three error dicts. The native call site SHOULD have
    # extracted these from ``previous_outputs[*]["result"]``.
    recent_results = [
        {"status": "error", "error": "Unknown tool name."},
        {"status": "error", "error": "Unknown tool name."},
        {"status": "error", "error": "Unknown tool name."},
    ]
    summary = agent._build_loop_break_summary(
        tool_name="mcp_tool_bad_tool",
        consecutive_count=4,
        step_results=recent_results,
    )
    # CRITICAL: must NOT claim success on loop-of-errors. This is the
    # exact bug found in eval-20260519-192836/tool_perf_normal_verbatim:
    # agent_response="Task completed with mcp_tool_quick_access_..." even
    # though every call returned "Unknown tool name".
    assert (
        "Task completed" not in summary
    ), f"loop-break must NOT claim success on errors; got: {summary!r}"
    assert "kept failing" in summary
    assert "Unknown tool name" in summary


def test_helper_rejects_wrapper_dicts_silently():
    """Defensive: WRAPPED ``previous_outputs`` shape (no ``status`` key)
    falls through to the success path. Documents the native-call-site
    bug shape — the unwrap must happen at the caller."""
    with patch("gaia.agents.base.agent.AgentSDK"):
        a = _DummyAgent(silent_mode=True, skip_lemonade=True)
    wrapped = [
        {"tool": "x", "args": {}, "result": {"status": "error", "error": "boom"}},
    ]
    summary = a._build_loop_break_summary(
        tool_name="x", consecutive_count=4, step_results=wrapped
    )
    assert "Task completed" in summary
