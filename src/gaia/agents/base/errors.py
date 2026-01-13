# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Generic error formatting for user-facing error messages.

This module provides utilities to format exceptions in a user-friendly way,
showing the user's code with a visual pointer to the error line while
filtering out framework internals.
"""

import json
import linecache
import textwrap
import traceback
from typing import List, Optional, Set

# Paths to filter out (framework internals)
FRAMEWORK_PATHS: Set[str] = {
    "gaia/agents/base",
    "gaia/agents/blender",
    "gaia/agents/chat",
    "gaia/agents/code",
    "gaia/agents/docker",
    "gaia/agents/jira",
    "gaia/agents/routing",
    "gaia/agents/tools",
    "site-packages/",
}


def format_user_error(
    exception: Exception,
    context_lines: int = 2,
) -> str:
    """
    Format an exception to show user's code with visual pointer.

    Filters out framework internals, shows only user code frames
    with source context around the error line.

    Args:
        exception: The caught exception
        context_lines: Lines of code context before/after error

    Returns:
        Formatted error string with traceback and code pointer

    Example output:
        KeyError: 'data'

        Traceback (most recent call last):
          File "my_agent.py", line 39, in get_big_llms
              37 | url = f"{base_url}/models?show_all=true"
              38 | response = requests.get(url, timeout=60)
          >>> 39 | models = response.json()["data"]
              40 |
              41 | top_5_models = sorted(
    """
    lines = []
    lines.append(f"{type(exception).__name__}: {exception}")
    lines.append("")

    # Extract traceback frames
    tb = traceback.extract_tb(exception.__traceback__)
    user_frames = _filter_user_frames(tb)

    if not user_frames:
        # No user frames found, show last frame as fallback
        if tb:
            user_frames = [tb[-1]]
        else:
            return "\n".join(lines)

    lines.append("Traceback (most recent call last):")

    for frame in user_frames:
        lines.append(f'  File "{frame.filename}", line {frame.lineno}, in {frame.name}')

        # Show code context
        code_context = _get_code_context(frame.filename, frame.lineno, context_lines)
        if code_context:
            lines.append(code_context)

    return "\n".join(lines)


def format_execution_trace(
    exception: Exception,
    query: Optional[str] = None,
    plan_step: Optional[int] = None,
    total_steps: Optional[int] = None,
    tool_name: Optional[str] = None,
    tool_args: Optional[dict] = None,
    context_lines: int = 5,
) -> str:
    """
    Format an exception with full execution trace for debugging.

    Shows the agent's execution path (Query → Plan → Tool → Error)
    along with the user's code context.

    Args:
        exception: The caught exception
        query: The original user query
        plan_step: Current step number in the plan (1-based)
        total_steps: Total number of steps in the plan
        tool_name: Name of the tool that failed
        tool_args: Arguments passed to the tool
        context_lines: Lines of code context before/after error

    Returns:
        Formatted error string with execution trace and code pointer
    """
    sep = "═" * 63
    lines = []

    # Header
    lines.append(sep)
    lines.append("AGENT ERROR - Tool execution failed")
    lines.append(sep)
    lines.append("")

    # Execution trace section
    lines.append("Execution Trace:")
    if query:
        # Truncate long queries
        display_query = query[:80] + "..." if len(query) > 80 else query
        lines.append(f'  Query: "{display_query}"')
    if plan_step is not None and total_steps is not None:
        lines.append(f"  Plan Step: {plan_step}/{total_steps}")
    if tool_name:
        lines.append(f"  Tool: {tool_name}")
    if tool_args:
        args_str = _truncate_args(tool_args)
        lines.append(f"  Args: {args_str}")
    lines.append("")

    # Error section
    lines.append("Error:")
    error_msg = f"{type(exception).__name__}: {exception}"
    # Word wrap long error messages (word-aware wrapping)
    wrapped_lines = textwrap.wrap(error_msg, width=70)
    for line in wrapped_lines:
        lines.append(f"  {line}")
    lines.append("")

    # Your Code section
    tb = traceback.extract_tb(exception.__traceback__)
    user_frames = _filter_user_frames(tb)

    if not user_frames and tb:
        # No user frames found, use last frame as fallback
        user_frames = [tb[-1]]

    if user_frames:
        lines.append("Your Code:")
        for frame in user_frames:
            lines.append(
                f'  File "{frame.filename}", line {frame.lineno}, in {frame.name}'
            )
            lines.append("")

            # Show code context with more lines
            code_context = _get_code_context(
                frame.filename, frame.lineno, context_lines
            )
            if code_context:
                lines.append(code_context)

    lines.append("")
    lines.append(sep)

    return "\n".join(lines)


def _filter_user_frames(
    frames: List[traceback.FrameSummary],
) -> List[traceback.FrameSummary]:
    """Filter out framework internal frames, keep user code."""
    return [
        f for f in frames if not any(path in f.filename for path in FRAMEWORK_PATHS)
    ]


def _get_code_context(
    filename: str,
    error_line: int,
    context: int = 2,
) -> Optional[str]:
    """Get source code context around error line with pointer."""
    lines = []

    for line_num in range(error_line - context, error_line + context + 1):
        if line_num < 1:
            continue

        code = linecache.getline(filename, line_num).rstrip()
        if not code and line_num != error_line:
            continue

        if line_num == error_line:
            # Visual pointer to error line
            lines.append(f"      >>> {line_num:4d} | {code}")
        else:
            lines.append(f"          {line_num:4d} | {code}")

    return "\n".join(lines) if lines else None


def _truncate_args(tool_args: Optional[dict], max_length: int = 100) -> str:
    """Truncate tool args while preserving structure where possible.

    Uses JSON formatting for cleaner output and truncates at character
    boundary with ellipsis indicator.

    Args:
        tool_args: Dictionary of tool arguments
        max_length: Maximum string length before truncation

    Returns:
        Formatted string representation of the arguments
    """
    if not tool_args:
        return "{}"

    try:
        # Use JSON for cleaner, more readable output
        args_str = json.dumps(tool_args, default=str)
    except (TypeError, ValueError):
        # Fallback to str() if JSON fails
        args_str = str(tool_args)

    if len(args_str) <= max_length:
        return args_str

    # Truncate but indicate it's truncated
    return args_str[: max_length - 3] + "..."
