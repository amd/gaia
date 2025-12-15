# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Python step implementations.

Steps wrap the existing Code Agent tools with standardized interfaces.
"""

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from .base import BaseStep, ErrorCategory, StepResult, UserContext


@dataclass
class CreateProjectStep(BaseStep):
    """Step to create a Python project."""

    name: str = "create_project"
    description: str = "Generate Python project"
    user_request: str = ""

    def get_tool_invocation(
        self, context: UserContext
    ) -> Optional[Tuple[str, Dict[str, Any]]]:
        """Return create_project invocation."""
        return (
            "create_project",
            {
                "query": self.user_request or context.user_request,
            },
        )

    def handle_result(self, result: Any, context: UserContext) -> StepResult:
        """Convert tool result to StepResult."""
        if isinstance(result, dict):
            if result.get("success"):
                # Store project info in context
                project_name = result.get("project_name", "")
                files = result.get("files", [])
                return StepResult.ok(
                    f"Project {project_name} created",
                    project_name=project_name,
                    files=files,
                )
            return StepResult.make_error(
                "Failed to create project",
                result.get("error", "Unknown error"),
                ErrorCategory.COMPILATION,
            )
        return StepResult.make_error(
            "Unexpected result format", str(result), ErrorCategory.UNKNOWN
        )


@dataclass
class ListFilesStep(BaseStep):
    """Step to list files in project."""

    name: str = "list_files"
    description: str = "List project files"

    def get_tool_invocation(
        self, context: UserContext
    ) -> Optional[Tuple[str, Dict[str, Any]]]:
        """Return list_files invocation."""
        # Get project name from previous step output
        project_name = context.step_outputs.get("create_project", {}).get(
            "project_name", ""
        )
        return (
            "list_files",
            {
                "path": project_name or context.project_dir,
            },
        )

    def handle_result(self, result: Any, context: UserContext) -> StepResult:
        """Convert tool result to StepResult."""
        if isinstance(result, dict):
            if result.get("success") or "files" in result:
                files = result.get("files", [])
                return StepResult.ok(
                    f"Found {len(files)} files",
                    files=files,
                )
            return StepResult.make_error(
                "Failed to list files",
                result.get("error", "Unknown error"),
                ErrorCategory.UNKNOWN,
            )
        # If result is a list directly
        if isinstance(result, list):
            return StepResult.ok(f"Found {len(result)} files", files=result)
        return StepResult.make_error(
            "Unexpected result format", str(result), ErrorCategory.UNKNOWN
        )


@dataclass
class ValidateProjectStep(BaseStep):
    """Step to validate project structure."""

    name: str = "validate_project"
    description: str = "Validate project structure"

    def get_tool_invocation(
        self, context: UserContext
    ) -> Optional[Tuple[str, Dict[str, Any]]]:
        """Return validate_project invocation."""
        project_name = context.step_outputs.get("create_project", {}).get(
            "project_name", ""
        )
        return (
            "validate_project",
            {
                "project_path": project_name or context.project_dir,
                "fix": True,
            },
        )

    def handle_result(self, result: Any, context: UserContext) -> StepResult:
        """Convert tool result to StepResult."""
        if isinstance(result, dict):
            if result.get("valid") or result.get("success"):
                return StepResult.ok("Project structure validated")
            issues = result.get("issues", [])
            return StepResult.warning(
                "Project has issues",
                issues=issues,
            )
        return StepResult.make_error(
            "Unexpected result format", str(result), ErrorCategory.UNKNOWN
        )


@dataclass
class AutoFixSyntaxStep(BaseStep):
    """Step to auto-fix syntax errors."""

    name: str = "auto_fix_syntax"
    description: str = "Fix syntax errors"

    def get_tool_invocation(
        self, context: UserContext
    ) -> Optional[Tuple[str, Dict[str, Any]]]:
        """Return auto_fix_syntax_errors invocation."""
        project_name = context.step_outputs.get("create_project", {}).get(
            "project_name", ""
        )
        return (
            "auto_fix_syntax_errors",
            {
                "project_path": project_name or context.project_dir,
            },
        )

    def handle_result(self, result: Any, context: UserContext) -> StepResult:
        """Convert tool result to StepResult."""
        if isinstance(result, dict):
            if result.get("success"):
                fixed_count = result.get("files_fixed", 0)
                if fixed_count > 0:
                    return StepResult.warning(
                        f"Fixed syntax errors in {fixed_count} files",
                        files_fixed=fixed_count,
                    )
                return StepResult.ok("No syntax errors found")
            return StepResult.make_error(
                "Failed to fix syntax errors",
                result.get("error", "Unknown error"),
                ErrorCategory.SYNTAX,
            )
        return StepResult.make_error(
            "Unexpected result format", str(result), ErrorCategory.UNKNOWN
        )


@dataclass
class AnalyzePylintStep(BaseStep):
    """Step to analyze code with pylint."""

    name: str = "analyze_pylint"
    description: str = "Run pylint analysis"

    def get_tool_invocation(
        self, context: UserContext
    ) -> Optional[Tuple[str, Dict[str, Any]]]:
        """Return analyze_with_pylint invocation."""
        project_name = context.step_outputs.get("create_project", {}).get(
            "project_name", ""
        )
        return (
            "analyze_with_pylint",
            {
                "file_path": project_name or context.project_dir,
            },
        )

    def handle_result(self, result: Any, context: UserContext) -> StepResult:
        """Convert tool result to StepResult."""
        if isinstance(result, dict):
            score = result.get("score", 0)
            issues = result.get("issues", [])

            if score >= 8.0:
                return StepResult.ok(f"Pylint score: {score}/10", score=score)
            if issues:
                return StepResult.warning(
                    f"Pylint score: {score}/10 ({len(issues)} issues)",
                    score=score,
                    issues=issues,
                )
            return StepResult.ok(
                f"Pylint completed with score: {score}/10", score=score
            )
        return StepResult.make_error(
            "Unexpected result format", str(result), ErrorCategory.UNKNOWN
        )


@dataclass
class FixLintingStep(BaseStep):
    """Step to fix linting issues."""

    name: str = "fix_linting"
    description: str = "Fix linting issues"

    def should_skip(self, context: UserContext) -> Optional[str]:
        """Skip if pylint score is already good."""
        pylint_output = context.step_outputs.get("analyze_pylint", {})
        score = pylint_output.get("score", 0)
        if score >= 8.0:
            return f"Pylint score {score}/10 is good, no fixing needed"
        return None

    def get_tool_invocation(
        self, context: UserContext
    ) -> Optional[Tuple[str, Dict[str, Any]]]:
        """Return fix_linting_errors invocation."""
        project_name = context.step_outputs.get("create_project", {}).get(
            "project_name", ""
        )
        return (
            "fix_linting_errors",
            {
                "project_path": project_name or context.project_dir,
            },
        )

    def handle_result(self, result: Any, context: UserContext) -> StepResult:
        """Convert tool result to StepResult."""
        if isinstance(result, dict):
            if result.get("success"):
                fixed_count = result.get("files_fixed", 0)
                return StepResult.ok(f"Fixed linting issues in {fixed_count} files")
            return StepResult.warning(
                "Some linting issues could not be auto-fixed",
                error=result.get("error", ""),
            )
        return StepResult.make_error(
            "Unexpected result format", str(result), ErrorCategory.UNKNOWN
        )


@dataclass
class RunPytestStep(BaseStep):
    """Step to run pytest."""

    name: str = "run_tests"
    description: str = "Run pytest"

    def get_tool_invocation(
        self, context: UserContext
    ) -> Optional[Tuple[str, Dict[str, Any]]]:
        """Return run_tests invocation."""
        project_name = context.step_outputs.get("create_project", {}).get(
            "project_name", ""
        )
        return (
            "run_tests",
            {
                "project_path": project_name or context.project_dir,
            },
        )

    def handle_result(self, result: Any, context: UserContext) -> StepResult:
        """Convert tool result to StepResult."""
        if isinstance(result, dict):
            tests_passed = result.get("tests_passed", False)
            return_code = result.get("return_code", 1)

            if tests_passed or return_code == 0:
                passed = result.get("passed", 0)
                return StepResult.ok(
                    f"All tests passed ({passed} tests)",
                    passed=passed,
                )
            failed = result.get("failed", 0)
            return StepResult.warning(
                f"Some tests failed ({failed} failures)",
                failed=failed,
                output=result.get("output", ""),
            )
        return StepResult.make_error(
            "Unexpected result format", str(result), ErrorCategory.UNKNOWN
        )
