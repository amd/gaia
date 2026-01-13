# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Assertion helpers for testing GAIA agents."""

from typing import Any, Dict, List, Optional, Union

from gaia.testing.mocks import MockLLMProvider, MockToolExecutor, MockVLMClient


def assert_llm_called(
    mock_llm: MockLLMProvider,
    times: Optional[int] = None,
    min_times: Optional[int] = None,
    max_times: Optional[int] = None,
) -> None:
    """
    Assert that the mock LLM was called.

    Args:
        mock_llm: MockLLMProvider instance
        times: Exact number of expected calls (optional)
        min_times: Minimum number of calls (optional)
        max_times: Maximum number of calls (optional)

    Raises:
        AssertionError: If call count doesn't match expectations

    Example:
        from gaia.testing import MockLLMProvider, assert_llm_called

        mock_llm = MockLLMProvider(responses=["Hello"])
        mock_llm.generate("Test")

        assert_llm_called(mock_llm)  # At least once
        assert_llm_called(mock_llm, times=1)  # Exactly once
        assert_llm_called(mock_llm, min_times=1, max_times=5)  # Range
    """
    call_count = mock_llm.call_count

    if times is not None:
        assert call_count == times, (
            f"Expected LLM to be called {times} time(s), "
            f"but was called {call_count} time(s)"
        )
    else:
        if min_times is None and max_times is None:
            # Just check it was called at least once
            assert call_count > 0, "Expected LLM to be called at least once"

        if min_times is not None:
            assert call_count >= min_times, (
                f"Expected LLM to be called at least {min_times} time(s), "
                f"but was called {call_count} time(s)"
            )

        if max_times is not None:
            assert call_count <= max_times, (
                f"Expected LLM to be called at most {max_times} time(s), "
                f"but was called {call_count} time(s)"
            )


def assert_llm_prompt_contains(
    mock_llm: MockLLMProvider,
    text: str,
    call_index: int = -1,
) -> None:
    """
    Assert that an LLM prompt contains specific text.

    Args:
        mock_llm: MockLLMProvider instance
        text: Text that should be in the prompt
        call_index: Which call to check (-1 = last call, 0 = first call)

    Raises:
        AssertionError: If text not found in prompt

    Example:
        assert_llm_prompt_contains(mock_llm, "customer")
        assert_llm_prompt_contains(mock_llm, "search", call_index=0)
    """
    assert mock_llm.call_history, "LLM was never called"

    call = mock_llm.call_history[call_index]
    prompt = call.get("prompt", "")

    assert (
        text in prompt
    ), f"Expected prompt to contain '{text}', but prompt was:\n{prompt[:500]}"


def assert_vlm_called(
    mock_vlm: MockVLMClient,
    times: Optional[int] = None,
) -> None:
    """
    Assert that the mock VLM was called.

    Args:
        mock_vlm: MockVLMClient instance
        times: Exact number of expected calls (optional)

    Raises:
        AssertionError: If call count doesn't match

    Example:
        assert_vlm_called(mock_vlm)  # At least once
        assert_vlm_called(mock_vlm, times=2)  # Exactly twice
    """
    call_count = mock_vlm.call_count

    if times is not None:
        assert call_count == times, (
            f"Expected VLM to be called {times} time(s), "
            f"but was called {call_count} time(s)"
        )
    else:
        assert call_count > 0, "Expected VLM to be called at least once"


def assert_tool_called(
    executor: MockToolExecutor,
    tool_name: str,
    times: Optional[int] = None,
) -> None:
    """
    Assert that a specific tool was called.

    Args:
        executor: MockToolExecutor instance
        tool_name: Name of the tool
        times: Exact number of expected calls (optional)

    Raises:
        AssertionError: If tool wasn't called or count doesn't match

    Example:
        from gaia.testing import MockToolExecutor, assert_tool_called

        executor = MockToolExecutor()
        executor.execute("search", {"query": "test"})

        assert_tool_called(executor, "search")
        assert_tool_called(executor, "search", times=1)
    """
    calls = executor.get_tool_calls(tool_name)

    if times is not None:
        assert len(calls) == times, (
            f"Expected tool '{tool_name}' to be called {times} time(s), "
            f"but was called {len(calls)} time(s)"
        )
    else:
        assert len(calls) > 0, (
            f"Expected tool '{tool_name}' to be called, but it was never called. "
            f"Tools called: {executor.tool_names_called}"
        )


def assert_tool_args(
    executor: MockToolExecutor,
    tool_name: str,
    expected_args: Dict[str, Any],
    call_index: int = 0,
) -> None:
    """
    Assert that a tool was called with specific arguments.

    Args:
        executor: MockToolExecutor instance
        tool_name: Name of the tool
        expected_args: Expected arguments (subset matching)
        call_index: Which call to check (0 = first call)

    Raises:
        AssertionError: If arguments don't match

    Example:
        executor.execute("search", {"query": "test", "limit": 10})
        assert_tool_args(executor, "search", {"query": "test"})
    """
    actual_args = executor.get_tool_args(tool_name, call_index)

    assert (
        actual_args is not None
    ), f"Tool '{tool_name}' was not called (or call_index {call_index} out of range)"

    for key, expected_value in expected_args.items():
        assert key in actual_args, (
            f"Expected argument '{key}' not found in tool call. "
            f"Actual args: {actual_args}"
        )
        assert actual_args[key] == expected_value, (
            f"Argument '{key}' mismatch. "
            f"Expected: {expected_value}, Actual: {actual_args[key]}"
        )


def assert_result_has_keys(
    result: Dict[str, Any],
    keys: List[str],
) -> None:
    """
    Assert that a result dictionary has specific keys.

    Args:
        result: Result dictionary to check
        keys: List of required keys

    Raises:
        AssertionError: If any key is missing

    Example:
        result = agent.process_query("test")
        assert_result_has_keys(result, ["answer", "steps_taken"])
    """
    assert isinstance(
        result, dict
    ), f"Expected result to be dict, got {type(result).__name__}"

    missing_keys = [key for key in keys if key not in result]
    if missing_keys:
        raise AssertionError(
            f"Result missing required keys: {missing_keys}. "
            f"Available keys: {list(result.keys())}"
        )


def assert_result_value(
    result: Dict[str, Any],
    key: str,
    expected: Any,
) -> None:
    """
    Assert that a result has a specific value for a key.

    Args:
        result: Result dictionary
        key: Key to check
        expected: Expected value

    Raises:
        AssertionError: If value doesn't match

    Example:
        assert_result_value(result, "status", "success")
    """
    assert key in result, f"Key '{key}' not found in result: {list(result.keys())}"
    actual = result[key]
    assert (
        actual == expected
    ), f"Value mismatch for key '{key}'. Expected: {expected}, Actual: {actual}"


def assert_agent_completed(
    result: Union[Dict[str, Any], str],
    has_answer: bool = True,
) -> None:
    """
    Assert that an agent completed processing successfully.

    Args:
        result: Result from agent.process_query()
        has_answer: Whether to check for an 'answer' key

    Raises:
        AssertionError: If agent didn't complete properly

    Example:
        result = agent.process_query("test")
        assert_agent_completed(result)
    """
    # Handle string results (some agents return strings directly)
    if isinstance(result, str):
        assert len(result) > 0, "Agent returned empty string"
        return

    assert isinstance(
        result, dict
    ), f"Expected result to be dict or str, got {type(result).__name__}"

    # Check for error indicators
    if "error" in result and result["error"]:
        raise AssertionError(f"Agent returned error: {result['error']}")

    if "status" in result and result["status"] == "error":
        error_msg = result.get("message", result.get("error", "Unknown error"))
        raise AssertionError(f"Agent returned error status: {error_msg}")

    # Check for answer if required
    if has_answer:
        assert "answer" in result or "response" in result or "result" in result, (
            "Agent result missing answer/response/result key. "
            f"Keys present: {list(result.keys())}"
        )


def assert_no_errors(result: Dict[str, Any]) -> None:
    """
    Assert that a result contains no errors.

    Args:
        result: Result dictionary

    Raises:
        AssertionError: If result contains error indicators

    Example:
        result = agent.process_query("test")
        assert_no_errors(result)
    """
    if not isinstance(result, dict):
        return  # Non-dict results don't have error keys

    # Check various error patterns
    if "error" in result and result["error"]:
        raise AssertionError(f"Result contains error: {result['error']}")

    if "errors" in result and result["errors"]:
        raise AssertionError(f"Result contains errors: {result['errors']}")

    if result.get("status") == "error":
        msg = result.get("message", result.get("error", "Unknown"))
        raise AssertionError(f"Result has error status: {msg}")

    if result.get("success") is False:
        msg = result.get("message", result.get("error", "Unknown"))
        raise AssertionError(f"Result indicates failure: {msg}")
