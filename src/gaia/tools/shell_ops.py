# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Shell execution tools for pipeline agents.

Provides sandboxed command execution and test-runner helpers.
"""

import subprocess

from gaia.agents.base.tools import tool

_MAX_OUTPUT_CHARS = 10_000


def _truncate(text: str, limit: int = _MAX_OUTPUT_CHARS) -> str:
    """Return *text* truncated to *limit* characters with a notice."""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [truncated at {limit} chars]"


@tool
def bash_execute(command: str, workspace_dir: str = ".", timeout: int = 120) -> dict:
    """Execute a shell command inside the workspace.

    Args:
        command: The shell command to run.
        workspace_dir: Working directory for the subprocess.
        timeout: Maximum seconds before the command is killed.

    Returns:
        A dict with ``status``, ``stdout``, ``stderr``, ``return_code``, and
        ``command`` on success, or ``status`` and ``error`` on timeout.
    """
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            cwd=workspace_dir,
            timeout=timeout,
        )
        return {
            "status": "success",
            "stdout": _truncate(result.stdout),
            "stderr": _truncate(result.stderr),
            "return_code": result.returncode,
            "command": command,
        }
    except subprocess.TimeoutExpired:
        return {
            "status": "error",
            "error": f"Command timed out after {timeout}s",
            "command": command,
        }
    except Exception as exc:
        return {"status": "error", "error": str(exc), "command": command}


@tool
def run_tests(test_path: str = ".", workspace_dir: str = ".", timeout: int = 120) -> dict:
    """Run pytest on the given path inside the workspace.

    Args:
        test_path: Relative path to the test file or directory.
        workspace_dir: Working directory for the subprocess.
        timeout: Maximum seconds before the test run is killed.

    Returns:
        A dict with ``status``, ``stdout``, ``stderr``, and ``return_code``
        on success, or ``status`` and ``error`` on timeout.
    """
    command = f"python -m pytest {test_path} -q --tb=short"
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            cwd=workspace_dir,
            timeout=timeout,
        )
        return {
            "status": "success",
            "stdout": _truncate(result.stdout),
            "stderr": _truncate(result.stderr),
            "return_code": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {
            "status": "error",
            "error": f"Command timed out after {timeout}s",
        }
    except Exception as exc:
        return {"status": "error", "error": str(exc)}
