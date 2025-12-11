# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Shell Tools Mixin for Chat Agent.

Provides shell command execution capabilities for file operations and system queries.
"""

import logging
import os
import shlex
import subprocess
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class ShellToolsMixin:
    """
    Mixin providing shell command execution tools with rate limiting.

    Tools provided:
    - run_shell_command: Execute terminal commands with timeout and safety checks

    Rate Limiting:
    - Max 10 commands per minute to prevent DOS
    - Max 3 commands per 10 seconds for burst prevention
    """

    def __init__(self, *args, **kwargs):
        """Initialize shell tools with rate limiting."""
        super().__init__(*args, **kwargs)

        # Rate limiting configuration
        self.shell_command_times = deque(maxlen=100)  # Track last 100 command times
        self.max_commands_per_minute = 10
        self.max_commands_per_10_seconds = 3

    def _check_rate_limit(self) -> tuple:
        """
        Check if rate limit allows another command.

        Returns:
            (allowed: bool, reason: str, wait_time: float)
        """
        # Initialize if not already done (defensive programming)
        if not hasattr(self, "shell_command_times"):
            self.shell_command_times = deque(maxlen=100)
            self.max_commands_per_minute = 10
            self.max_commands_per_10_seconds = 3

        current_time = time.time()

        # Remove old timestamps outside the window
        minute_ago = current_time - 60
        ten_sec_ago = current_time - 10

        # Count recent commands
        recent_minute = sum(1 for t in self.shell_command_times if t > minute_ago)
        recent_10_sec = sum(1 for t in self.shell_command_times if t > ten_sec_ago)

        # Check 10-second burst limit
        if recent_10_sec >= self.max_commands_per_10_seconds:
            recent_times = [t for t in self.shell_command_times if t > ten_sec_ago]
            if recent_times:
                oldest_in_window = min(recent_times)
                wait_time = 10 - (current_time - oldest_in_window)
            else:
                wait_time = 10.0
            return (
                False,
                f"Rate limit: max {self.max_commands_per_10_seconds} commands per 10 seconds. Wait {wait_time:.1f}s",
                wait_time,
            )

        # Check 1-minute limit
        if recent_minute >= self.max_commands_per_minute:
            recent_times = [t for t in self.shell_command_times if t > minute_ago]
            if recent_times:
                oldest_in_window = min(recent_times)
                wait_time = 60 - (current_time - oldest_in_window)
            else:
                wait_time = 60.0
            return (
                False,
                f"Rate limit: max {self.max_commands_per_minute} commands per minute. Wait {wait_time:.1f}s",
                wait_time,
            )

        return True, "", 0.0

    def _record_command_execution(self):
        """Record command execution timestamp for rate limiting."""
        self.shell_command_times.append(time.time())

    def register_shell_tools(self) -> None:
        """Register shell command execution tools."""
        from gaia.agents.base.tools import tool

        @tool(
            name="run_shell_command",
            description="Execute a shell/terminal command. Useful for listing directories (ls/dir), checking files (cat, stat), finding files (find), text processing (grep, head, tail), and navigation (pwd).",
            parameters={
                "command": {
                    "type": "str",
                    "description": "The shell command to execute (e.g., 'ls -la', 'pwd', 'cat file.txt')",
                    "required": True,
                },
                "working_directory": {
                    "type": "str",
                    "description": "Directory to run the command in (defaults to current directory)",
                    "required": False,
                },
                "timeout": {
                    "type": "int",
                    "description": "Timeout in seconds (default: 30)",
                    "required": False,
                },
            },
        )
        def run_shell_command(
            command: str, working_directory: Optional[str] = None, timeout: int = 30
        ) -> Dict[str, Any]:
            """
            Execute a shell command and return the output.

            Args:
                command: Shell command to execute
                working_directory: Directory to run command in
                timeout: Maximum execution time in seconds

            Returns:
                Dictionary with status, output, and error information
            """
            try:
                # Check rate limits first to prevent DOS
                allowed, reason, wait_time = self._check_rate_limit()
                if not allowed:
                    return {
                        "status": "error",
                        "error": f"{reason}. Please wait {wait_time:.1f} seconds.",
                        "has_errors": True,
                        "rate_limited": True,
                        "wait_time_seconds": wait_time,
                        "hint": "Rate limiting prevents excessive command execution",
                    }

                # Validate working directory if specified
                if working_directory:
                    if not os.path.exists(working_directory):
                        return {
                            "status": "error",
                            "error": f"Working directory not found: {working_directory}",
                            "has_errors": True,
                        }

                    if not os.path.isdir(working_directory):
                        return {
                            "status": "error",
                            "error": f"Path is not a directory: {working_directory}",
                            "has_errors": True,
                        }

                    # Validate path is allowed
                    # Use PathValidator if available (ChatAgent), otherwise fallback or skip
                    if hasattr(self, "path_validator"):
                        if not self.path_validator.is_path_allowed(working_directory):
                            return {
                                "status": "error",
                                "error": f"Access denied: {working_directory} is not in allowed paths",
                                "has_errors": True,
                            }
                    elif hasattr(self, "_is_path_allowed"):
                        # Backward compatibility
                        if not self._is_path_allowed(working_directory):
                            return {
                                "status": "error",
                                "error": f"Access denied: {working_directory} is not in allowed paths",
                                "has_errors": True,
                            }

                    cwd = str(Path(working_directory).resolve())
                else:
                    cwd = str(Path.cwd())

                # Parse command safely
                try:
                    cmd_parts = shlex.split(command)
                except ValueError as e:
                    return {
                        "status": "error",
                        "error": f"Invalid command syntax: {e}",
                        "has_errors": True,
                    }

                if not cmd_parts:
                    return {
                        "status": "error",
                        "error": "Empty command",
                        "has_errors": True,
                    }

                # Validate arguments for path traversal
                # This prevents "cat ../secret.txt" even if "cat" is allowed
                if hasattr(self, "path_validator"):
                    for arg in cmd_parts[1:]:
                        # Skip flags that don't look like paths (simple heuristics)
                        # We check for path separators or ".."
                        # We also handle --flag=/path/to/file

                        candidate_path = arg
                        if arg.startswith("-"):
                            if "=" in arg:
                                _, candidate_path = arg.split("=", 1)
                            else:
                                # Skip flags without value (e.g. -l, --verbose)
                                # But what about -f/path? Hard to parse without knowing the tool.
                                # We'll assume if it has a path separator, it might be a path attached to a flag
                                if os.sep not in arg and "/" not in arg:
                                    continue
                                # If it has separators, treat the whole thing or part of it as path?
                                # Treating "-f/tmp" as a path "/tmp" is hard.
                                # Let's be conservative: if it contains separators, check it.

                        # Check if it looks like a path
                        if (
                            os.sep in candidate_path
                            or "/" in candidate_path
                            or ".." in candidate_path
                        ):
                            # Ignore URLs
                            if candidate_path.startswith(
                                ("http://", "https://", "git://", "ssh://")
                            ):
                                continue

                            # Resolve path relative to CWD
                            try:
                                # Handle potential flag prefix if we didn't split it cleanly
                                # This is best-effort.
                                clean_path = candidate_path

                                # Resolve
                                resolved_path = str(
                                    Path(cwd).joinpath(clean_path).resolve()
                                )

                                if not self.path_validator.is_path_allowed(
                                    resolved_path
                                ):
                                    return {
                                        "status": "error",
                                        "error": f"Access denied: Argument '{arg}' resolves to forbidden path '{resolved_path}'",
                                        "has_errors": True,
                                    }
                            except Exception:
                                # If we can't resolve it (e.g. invalid chars), we might warn or ignore.
                                # For security, maybe ignore if it's not a valid path anyway?
                                pass

                # Security: WHITELIST approach - only allow explicitly safe commands
                # This is much safer than a blacklist which always misses dangerous commands
                ALLOWED_COMMANDS = {
                    # File listing and navigation (READ-ONLY)
                    "ls",
                    "dir",
                    "pwd",
                    "cd",
                    # File content viewing (READ-ONLY)
                    "cat",
                    "head",
                    "tail",
                    "more",
                    "less",
                    # Text processing (READ-ONLY)
                    "grep",
                    "find",
                    "wc",
                    "sort",
                    "uniq",
                    "diff",
                    # File information (READ-ONLY)
                    "file",
                    "stat",
                    "du",
                    "df",
                    # System information (READ-ONLY)
                    "whoami",
                    "hostname",
                    "uname",
                    "date",
                    "uptime",
                    # Path utilities
                    "which",
                    "whereis",
                    "basename",
                    "dirname",
                    # Safe output
                    "echo",
                    "printf",
                    # Process information (READ-ONLY)
                    "ps",
                    "top",
                    "jobs",
                    # Git commands (mostly safe, read-only operations)
                    "git",  # Individual git subcommands checked separately
                }

                cmd_base = cmd_parts[0].lower()

                # Special handling for git - only allow read-only operations
                if cmd_base == "git":
                    if len(cmd_parts) > 1:
                        git_subcmd = cmd_parts[1].lower()
                        safe_git_commands = {
                            "status",
                            "log",
                            "show",
                            "diff",
                            "branch",
                            "remote",
                            "ls-files",
                            "ls-tree",
                            "describe",
                            "rev-parse",
                            "config",
                            "help",
                        }
                        if git_subcmd not in safe_git_commands:
                            return {
                                "status": "error",
                                "error": f"Git command '{git_subcmd}' is not allowed. Only read-only git operations are permitted.",
                                "has_errors": True,
                                "allowed_git_commands": list(safe_git_commands),
                            }
                elif cmd_base not in ALLOWED_COMMANDS:
                    return {
                        "status": "error",
                        "error": f"Command '{cmd_base}' is not in the allowed list for security reasons",
                        "has_errors": True,
                        "hint": "Only read-only, informational commands are allowed",
                        "examples": "ls, cat, grep, find, git status, etc.",
                    }

                # Log command execution (debug mode)
                if hasattr(self, "debug") and self.debug:
                    logger.info(f"Executing command: {command} in {cwd}")

                # Execute command
                start_time = datetime.utcnow()
                try:
                    result = subprocess.run(
                        cmd_parts,
                        cwd=cwd,
                        capture_output=True,
                        text=True,
                        timeout=timeout,
                        check=False,
                        env=os.environ.copy(),
                    )
                    duration = (datetime.utcnow() - start_time).total_seconds()

                    # Record successful command execution for rate limiting
                    self._record_command_execution()
                except subprocess.TimeoutExpired as exc:
                    duration = (datetime.utcnow() - start_time).total_seconds()

                    # Handle timeout gracefully
                    stdout_str = ""
                    stderr_str = ""
                    if exc.stdout:
                        stdout_str = (
                            exc.stdout
                            if isinstance(exc.stdout, str)
                            else exc.stdout.decode("utf-8", errors="replace")
                        )
                    if exc.stderr:
                        stderr_str = (
                            exc.stderr
                            if isinstance(exc.stderr, str)
                            else exc.stderr.decode("utf-8", errors="replace")
                        )

                    return {
                        "status": "error",
                        "error": f"Command timed out after {timeout} seconds",
                        "command": command,
                        "stdout": stdout_str,
                        "stderr": stderr_str,
                        "has_errors": True,
                        "timed_out": True,
                        "timeout": timeout,
                        "duration_seconds": duration,
                        "cwd": cwd,
                    }

                # Capture and truncate output if too long
                stdout = result.stdout or ""
                stderr = result.stderr or ""
                truncated = False
                max_output = 10_000

                if len(stdout) > max_output:
                    stdout = stdout[:max_output] + "\n...output truncated (stdout)..."
                    truncated = True

                if len(stderr) > max_output:
                    stderr = stderr[:max_output] + "\n...output truncated (stderr)..."
                    truncated = True

                # Debug logging
                if hasattr(self, "debug") and self.debug:
                    logger.info(
                        f"Command completed in {duration:.2f}s with return code {result.returncode}"
                    )

                return {
                    "status": "success",
                    "command": command,
                    "stdout": stdout,
                    "stderr": stderr,
                    "return_code": result.returncode,
                    "has_errors": result.returncode != 0,
                    "duration_seconds": duration,
                    "timeout": timeout,
                    "cwd": cwd,
                    "output_truncated": truncated,
                }

            except Exception as exc:
                logger.error(f"Error executing shell command: {exc}")
                return {"status": "error", "error": str(exc), "has_errors": True}
