# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Testing tools mixin for Code Agent."""

import os
import shlex
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


class TestingMixin:
    """Mixin providing Python code execution and testing tools.

    This mixin provides tools for:
    - Executing Python files as subprocesses
    - Running pytest test suites
    - Capturing and analyzing execution output
    - Timeout management for long-running processes

    Tools provided:
    - execute_python_file: Execute Python file with arguments and capture output
    - run_tests: Run pytest test suite for a project with timeout and error handling
    """

    def register_testing_tools(self) -> None:
        """Register testing tools."""
        from gaia.agents.base.tools import tool

        @tool
        def execute_python_file(
            file_path: str,
            args: Optional[List[str]] = None,
            timeout: int = 60,
            working_directory: Optional[str] = None,
            env_vars: Optional[Dict[str, str]] = None,
        ) -> Dict[str, Any]:
            """Execute a Python file as a subprocess and capture output.

            Args:
                file_path: Path to the Python file to execute.
                args: Optional CLI arguments (list or space-delimited string).
                timeout: Seconds to wait before aborting execution.
                working_directory: Directory to run the command from.
                env_vars: Additional environment variables to inject.

            Returns:
                Dictionary with execution metadata and captured output.
            """
            try:
                path = Path(file_path)
                if not path.exists():
                    return {
                        "status": "error",
                        "error": f"File not found: {file_path}",
                        "has_errors": True,
                    }
                if not path.is_file():
                    return {
                        "status": "error",
                        "error": f"Path is not a file: {file_path}",
                        "has_errors": True,
                    }

                resolved_file = str(path.resolve())
                cmd = [sys.executable, resolved_file]

                if args:
                    if isinstance(args, str):
                        extra_args = shlex.split(args)
                    elif isinstance(args, list):
                        extra_args = [str(a) for a in args]
                    else:
                        return {
                            "status": "error",
                            "error": "args must be a list of strings or a string",
                            "has_errors": True,
                        }
                    cmd.extend(extra_args)

                if working_directory:
                    wd_path = Path(working_directory)
                    if not wd_path.exists():
                        return {
                            "status": "error",
                            "error": f"Working directory not found: {working_directory}",
                            "has_errors": True,
                        }
                    if not wd_path.is_dir():
                        return {
                            "status": "error",
                            "error": f"Working directory is not a directory: {working_directory}",
                            "has_errors": True,
                        }
                    cwd = str(wd_path.resolve())
                else:
                    cwd = str(path.parent.resolve())

                env = os.environ.copy()
                if env_vars:
                    env.update({key: str(value) for key, value in env_vars.items()})

                start_time = datetime.utcnow()
                try:
                    result = subprocess.run(
                        cmd,
                        cwd=cwd,
                        env=env,
                        capture_output=True,
                        text=True,
                        timeout=timeout,
                        check=False,
                    )
                    duration = (datetime.utcnow() - start_time).total_seconds()
                except subprocess.TimeoutExpired as exc:
                    duration = (datetime.utcnow() - start_time).total_seconds()
                    # Ensure stdout/stderr are strings, not bytes
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
                        "error": f"Execution timed out after {timeout} seconds",
                        "file_path": resolved_file,
                        "command": " ".join(shlex.quote(part) for part in cmd),
                        "stdout": stdout_str,
                        "stderr": stderr_str,
                        "has_errors": True,
                        "timed_out": True,
                        "timeout": timeout,
                        "duration_seconds": duration,
                        "cwd": cwd,
                    }

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

                return {
                    "status": "success",
                    "file_path": resolved_file,
                    "command": " ".join(shlex.quote(part) for part in cmd),
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
                return {
                    "status": "error",
                    "error": str(exc),
                    "has_errors": True,
                }

        @tool
        def run_tests(
            project_path: str = ".",
            pytest_args: Optional[List[str]] = None,
            timeout: int = 120,
            env_vars: Optional[Dict[str, str]] = None,
        ) -> Dict[str, Any]:
            """Run pytest for the specified project directory."""
            try:
                project_dir = Path(project_path).resolve()
                if not project_dir.exists():
                    return {
                        "status": "error",
                        "error": f"Project path not found: {project_path}",
                    }
                if not project_dir.is_dir():
                    return {
                        "status": "error",
                        "error": f"Project path is not a directory: {project_path}",
                    }

                if isinstance(pytest_args, str):
                    extra_args = shlex.split(pytest_args)
                elif isinstance(pytest_args, list):
                    extra_args = [str(arg) for arg in pytest_args]
                elif pytest_args is None:
                    extra_args = []
                else:
                    return {
                        "status": "error",
                        "error": "pytest_args must be a list of strings or a string",
                    }

                cmd = [sys.executable, "-m", "pytest"]
                if not extra_args:
                    # Default to running the entire suite quietly to keep output manageable
                    extra_args = ["-q"]
                cmd.extend(extra_args)

                env = os.environ.copy()
                if env_vars:
                    env.update({key: str(value) for key, value in env_vars.items()})

                existing_pythonpath = env.get("PYTHONPATH")
                project_pythonpath = str(project_dir)
                if existing_pythonpath:
                    env["PYTHONPATH"] = (
                        f"{project_pythonpath}{os.pathsep}{existing_pythonpath}"
                    )
                else:
                    env["PYTHONPATH"] = project_pythonpath

                start_time = datetime.utcnow()
                try:
                    result = subprocess.run(
                        cmd,
                        cwd=str(project_dir),
                        env=env,
                        capture_output=True,
                        text=True,
                        timeout=timeout,
                        check=False,
                    )
                    duration = (datetime.utcnow() - start_time).total_seconds()
                except subprocess.TimeoutExpired as exc:
                    duration = (datetime.utcnow() - start_time).total_seconds()
                    # Ensure stdout/stderr are strings, not bytes
                    stdout_str = ""
                    stderr_str = ""
                    if hasattr(exc, "stdout") and exc.stdout:
                        stdout_str = (
                            exc.stdout
                            if isinstance(exc.stdout, str)
                            else exc.stdout.decode("utf-8", errors="replace")
                        )
                    if hasattr(exc, "stderr") and exc.stderr:
                        stderr_str = (
                            exc.stderr
                            if isinstance(exc.stderr, str)
                            else exc.stderr.decode("utf-8", errors="replace")
                        )

                    return {
                        "status": "error",
                        "error": f"pytest timed out after {timeout} seconds",
                        "project_path": str(project_dir),
                        "command": " ".join(shlex.quote(part) for part in cmd),
                        "stdout": stdout_str,
                        "stderr": stderr_str,
                        "tests_passed": False,
                        "timed_out": True,
                        "timeout": timeout,
                        "duration_seconds": duration,
                    }

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

                # Parse pytest output for failure count
                failure_summary = ""
                if result.returncode != 0:
                    # Look for pytest summary line like "7 failed, 75 passed"
                    import re

                    summary_match = re.search(r"(\d+)\s+failed", stdout)
                    if summary_match:
                        num_failed = summary_match.group(1)
                        failure_summary = (
                            f"{num_failed} test(s) failed - check stdout for details"
                        )
                    else:
                        failure_summary = "Tests failed - check stdout for details"

                return {
                    "status": "success",
                    "project_path": str(project_dir),
                    "command": " ".join(shlex.quote(part) for part in cmd),
                    "stdout": stdout,
                    "stderr": stderr,
                    "return_code": result.returncode,
                    "tests_passed": result.returncode == 0,
                    "failure_summary": (
                        failure_summary if not result.returncode == 0 else ""
                    ),
                    "duration_seconds": duration,
                    "timeout": timeout,
                    "output_truncated": truncated,
                }
            except Exception as exc:
                return {"status": "error", "error": str(exc)}
