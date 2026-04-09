# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Code-oriented tools for pipeline agents.

Provides codebase search and safe git operations.
"""

import os
import re
import subprocess

from gaia.agents.base.tools import tool

# Git sub-commands that are safe for agents to run.
_SAFE_GIT_SUBCOMMANDS = frozenset(
    {
        "status",
        "diff",
        "log",
        "show",
        "branch",
        "add",
        "commit",
        "stash",
        "tag",
        "ls-files",
        "rev-parse",
    }
)

# Patterns that must never appear in a git command string.
_DANGEROUS_PATTERNS = [
    r"push\s+--force",
    r"push\s+-f\b",
    r"reset\s+--hard",
    r"clean\s+-f",
]


@tool
def search_codebase(
    pattern: str,
    workspace_dir: str = ".",
    file_extension: str = ".py",
    max_results: int = 50,
) -> dict:
    """Search files in the workspace for a text pattern.

    Args:
        pattern: Regular-expression pattern to search for.
        workspace_dir: Root directory to walk.
        file_extension: Only inspect files whose name ends with this suffix.
        max_results: Stop collecting after this many matches.

    Returns:
        A dict with ``status``, ``pattern``, ``matches`` (list of dicts with
        ``file``, ``line``, ``text``), and ``total_matches``.
    """
    matches = []
    try:
        compiled = re.compile(pattern)
    except re.error as exc:
        return {"status": "error", "error": f"Invalid regex: {exc}"}

    try:
        for dirpath, _dirnames, filenames in os.walk(workspace_dir):
            for filename in filenames:
                if not filename.endswith(file_extension):
                    continue
                filepath = os.path.join(dirpath, filename)
                rel_path = os.path.relpath(filepath, workspace_dir)
                try:
                    with open(filepath, "r", encoding="utf-8", errors="ignore") as fh:
                        for line_num, line_text in enumerate(fh, start=1):
                            if compiled.search(line_text):
                                matches.append(
                                    {
                                        "file": rel_path,
                                        "line": line_num,
                                        "text": line_text.rstrip("\n"),
                                    }
                                )
                                if len(matches) >= max_results:
                                    return {
                                        "status": "success",
                                        "pattern": pattern,
                                        "matches": matches,
                                        "total_matches": len(matches),
                                    }
                except (OSError, UnicodeDecodeError):
                    continue

        return {
            "status": "success",
            "pattern": pattern,
            "matches": matches,
            "total_matches": len(matches),
        }
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


@tool
def git_operations(command: str, workspace_dir: str = ".") -> dict:
    """Run a safe git command inside the workspace.

    Only an allow-listed set of git sub-commands is permitted.  Dangerous
    patterns such as ``push --force``, ``reset --hard``, and ``clean -f``
    are explicitly blocked.

    Args:
        command: The git sub-command and arguments (without the leading
            ``git`` prefix).  Example: ``"status"`` or ``"log --oneline -5"``.
        workspace_dir: Working directory for the subprocess.

    Returns:
        A dict with ``status``, ``stdout``, ``stderr``, and ``return_code``
        on success, or ``status`` and ``error`` when the command is blocked.
    """
    # Determine the sub-command (first token).
    parts = command.strip().split()
    if not parts:
        return {"status": "error", "error": "Empty git command"}

    subcommand = parts[0]
    if subcommand not in _SAFE_GIT_SUBCOMMANDS:
        return {
            "status": "error",
            "error": (
                f"Git sub-command '{subcommand}' is not allowed. "
                f"Permitted: {', '.join(sorted(_SAFE_GIT_SUBCOMMANDS))}"
            ),
        }

    # Guard against dangerous flag combinations even within allowed
    # sub-commands (e.g., someone passing "status && push --force").
    full_command = f"git {command}"
    for dangerous in _DANGEROUS_PATTERNS:
        if re.search(dangerous, full_command):
            return {
                "status": "error",
                "error": f"Dangerous operation blocked: {full_command}",
            }

    try:
        result = subprocess.run(
            full_command,
            shell=True,
            capture_output=True,
            text=True,
            cwd=workspace_dir,
            timeout=30,
        )
        return {
            "status": "success",
            "stdout": result.stdout,
            "stderr": result.stderr,
            "return_code": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"status": "error", "error": "Git command timed out after 30s"}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}
