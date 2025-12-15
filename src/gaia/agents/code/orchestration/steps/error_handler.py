# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Three-tier error recovery for orchestration workflows.

Provides intelligent error handling with:
- RETRY: For transient errors (network, resources)
- FIX_AND_RETRY: For known fixable errors (missing deps, compilation)
- ESCALATE: For complex errors requiring LLM intervention
"""

import logging
import re
import time
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional, Tuple

from .base import ErrorCategory

logger = logging.getLogger(__name__)


class RecoveryAction(Enum):
    """Actions the error handler can take."""

    RETRY = auto()  # Wait and retry same operation
    FIX_AND_RETRY = auto()  # Execute fix, then retry
    ESCALATE = auto()  # Use LLM to diagnose and fix
    ABORT = auto()  # Give up, unrecoverable


@dataclass
class ErrorPattern:
    """Pattern for matching and handling specific errors."""

    pattern: str  # Regex pattern to match
    category: ErrorCategory
    action: RecoveryAction
    fix_command: Optional[str] = None  # Command to run for FIX_AND_RETRY
    max_retries: int = 2
    delay_seconds: float = 1.0


# Error patterns based on existing cli_tools.py ERROR_PATTERNS
ERROR_PATTERNS: List[ErrorPattern] = [
    # Network errors - retry
    ErrorPattern(
        pattern=r"ECONNREFUSED|ETIMEDOUT|ENOTFOUND|network\s+error",
        category=ErrorCategory.NETWORK,
        action=RecoveryAction.RETRY,
        max_retries=3,
        delay_seconds=2.0,
    ),
    # Resource errors - retry with delay
    ErrorPattern(
        pattern=r"ENOSPC|ENOMEM|out\s+of\s+memory",
        category=ErrorCategory.RESOURCE,
        action=RecoveryAction.RETRY,
        max_retries=2,
        delay_seconds=5.0,
    ),
    # Missing dependencies - fix and retry
    ErrorPattern(
        pattern=r"Cannot find module '([^']+)'|Module not found.*'([^']+)'",
        category=ErrorCategory.DEPENDENCY,
        action=RecoveryAction.FIX_AND_RETRY,
        fix_command="npm install {module}",
        max_retries=2,
    ),
    ErrorPattern(
        pattern=r"ModuleNotFoundError:\s+No module named '([^']+)'",
        category=ErrorCategory.DEPENDENCY,
        action=RecoveryAction.FIX_AND_RETRY,
        fix_command="pip install {module}",
        max_retries=2,
    ),
    # TypeScript compilation - escalate to LLM
    ErrorPattern(
        pattern=r"TS\d+:|error TS\d+|Type '.*' is not assignable",
        category=ErrorCategory.COMPILATION,
        action=RecoveryAction.ESCALATE,
        max_retries=1,
    ),
    # Prisma errors - fix and retry
    ErrorPattern(
        pattern=r"prisma generate|@prisma/client.*not.*generated",
        category=ErrorCategory.DEPENDENCY,
        action=RecoveryAction.FIX_AND_RETRY,
        fix_command="npx prisma generate",
        max_retries=2,
    ),
    # Syntax errors - escalate to LLM
    ErrorPattern(
        pattern=r"SyntaxError:|Unexpected token|Parse error",
        category=ErrorCategory.SYNTAX,
        action=RecoveryAction.ESCALATE,
        max_retries=1,
    ),
    # Lint errors - escalate to LLM for fix
    ErrorPattern(
        pattern=r"eslint.*error|warning.*react-hooks|unused variable",
        category=ErrorCategory.VALIDATION,
        action=RecoveryAction.ESCALATE,
        max_retries=1,
    ),
    # CSS content type errors (Issue #1002) - escalate to LLM
    # Catches TypeScript/JavaScript code in CSS files
    ErrorPattern(
        pattern=r"CSS file contains.*TypeScript|CRITICAL.*CSS file|globals\.css contains",
        category=ErrorCategory.VALIDATION,
        action=RecoveryAction.ESCALATE,
        max_retries=2,  # Allow retries - LLM can regenerate correct CSS
    ),
]


class ErrorHandler:
    """Handles errors with three-tier recovery strategy."""

    def __init__(
        self,
        command_executor: Optional[Callable[[str], Tuple[int, str]]] = None,
        llm_fixer: Optional[Callable[[str, str], Optional[str]]] = None,
    ):
        """Initialize error handler.

        Args:
            command_executor: Function to run shell commands (returns exit_code, output)
            llm_fixer: Function to fix code using LLM (takes error, code, returns fixed code)
        """
        self.command_executor = command_executor
        self.llm_fixer = llm_fixer
        self.retry_counts: Dict[str, int] = {}

    def categorize_error(self, error_text: str) -> Tuple[ErrorCategory, ErrorPattern]:
        """Categorize an error and find matching pattern.

        Args:
            error_text: The error message to categorize

        Returns:
            Tuple of (ErrorCategory, matching ErrorPattern or default)
        """
        for pattern in ERROR_PATTERNS:
            if re.search(pattern.pattern, error_text, re.IGNORECASE):
                return pattern.category, pattern

        # Default pattern for unknown errors
        return ErrorCategory.UNKNOWN, ErrorPattern(
            pattern=".*",
            category=ErrorCategory.UNKNOWN,
            action=RecoveryAction.ESCALATE,
            max_retries=1,
        )

    def handle_error(
        self,
        step_name: str,
        error_text: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Tuple[RecoveryAction, Optional[str]]:
        """Handle an error with appropriate recovery action.

        Args:
            step_name: Name of the step that failed
            error_text: The error message
            context: Optional context (e.g., code being executed)

        Returns:
            Tuple of (action to take, optional fix result/message)
        """
        category, pattern = self.categorize_error(error_text)
        retry_key = f"{step_name}:{category.name}"

        # Check retry count
        current_retries = self.retry_counts.get(retry_key, 0)
        if current_retries >= pattern.max_retries:
            logger.warning(
                f"Max retries ({pattern.max_retries}) exceeded for {step_name}"
            )
            return RecoveryAction.ABORT, f"Max retries exceeded: {error_text}"

        self.retry_counts[retry_key] = current_retries + 1

        logger.info(
            f"Error recovery: {category.name} -> {pattern.action.name} "
            f"(attempt {current_retries + 1}/{pattern.max_retries})"
        )

        if pattern.action == RecoveryAction.RETRY:
            return self._handle_retry(pattern)

        if pattern.action == RecoveryAction.FIX_AND_RETRY:
            return self._handle_fix_and_retry(pattern, error_text)

        if pattern.action == RecoveryAction.ESCALATE:
            return self._handle_escalate(error_text, context)

        return RecoveryAction.ABORT, error_text

    def _handle_retry(
        self, pattern: ErrorPattern
    ) -> Tuple[RecoveryAction, Optional[str]]:
        """Handle retry action with delay."""
        if pattern.delay_seconds > 0:
            logger.info(f"Waiting {pattern.delay_seconds}s before retry...")
            time.sleep(pattern.delay_seconds)
        return RecoveryAction.RETRY, None

    def _handle_fix_and_retry(
        self, pattern: ErrorPattern, error_text: str
    ) -> Tuple[RecoveryAction, Optional[str]]:
        """Handle fix-and-retry action."""
        if not pattern.fix_command or not self.command_executor:
            return RecoveryAction.ESCALATE, "No fix command available"

        # Extract module name from error if applicable
        fix_cmd = pattern.fix_command
        match = re.search(pattern.pattern, error_text, re.IGNORECASE)
        if match and match.groups():
            # Use first captured group as module name
            module = (
                match.group(1) or match.group(2)
                if len(match.groups()) > 1
                else match.group(1)
            )
            if module:
                fix_cmd = fix_cmd.format(module=module)

        logger.info(f"Executing fix command: {fix_cmd}")
        exit_code, output = self.command_executor(fix_cmd)

        if exit_code == 0:
            return RecoveryAction.RETRY, f"Fix applied: {fix_cmd}"
        return RecoveryAction.ESCALATE, f"Fix failed: {output}"

    def _handle_escalate(
        self, error_text: str, context: Optional[Dict[str, Any]] = None
    ) -> Tuple[RecoveryAction, Optional[str]]:
        """Handle escalation to LLM.

        For TypeScript errors, parses file path from error and reads content.
        """
        from pathlib import Path

        if not self.llm_fixer:
            return RecoveryAction.ABORT, "No LLM fixer available"

        code = context.get("code", "") if context else ""
        project_dir = context.get("project_dir", "") if context else ""
        error_file_path = None

        # If no code provided, try to extract file path from TypeScript error
        # Format: filename.ts(line,col): error TSxxxx: message
        if not code and project_dir:
            ts_error_match = re.search(
                r"^([^\s(]+\.tsx?)\(\d+,\d+\):", error_text, re.MULTILINE
            )
            if ts_error_match:
                error_file_path = ts_error_match.group(1)
                full_path = Path(project_dir) / error_file_path
                if full_path.exists():
                    try:
                        code = full_path.read_text(encoding="utf-8")
                        logger.info(f"Read file for LLM fix: {error_file_path}")
                    except Exception as e:
                        logger.warning(f"Could not read {error_file_path}: {e}")

        fixed_code = self.llm_fixer(error_text, code)

        if fixed_code:
            # If we read a file, write the fix back
            if error_file_path and project_dir:
                full_path = Path(project_dir) / error_file_path
                try:
                    full_path.write_text(fixed_code, encoding="utf-8")
                    logger.info(f"Wrote LLM fix to: {error_file_path}")
                except Exception as e:
                    logger.warning(f"Could not write fix to {error_file_path}: {e}")

            return RecoveryAction.RETRY, fixed_code
        return RecoveryAction.ABORT, "LLM could not fix the error"

    def reset_retry_count(self, step_name: str) -> None:
        """Reset retry count for a step after success."""
        keys_to_remove = [k for k in self.retry_counts if k.startswith(f"{step_name}:")]
        for key in keys_to_remove:
            del self.retry_counts[key]

    def get_recovery_suggestion(self, error_text: str) -> str:
        """Get a human-readable recovery suggestion for an error.

        Args:
            error_text: The error message

        Returns:
            Suggestion string for how to fix the error
        """
        category, pattern = self.categorize_error(error_text)

        suggestions = {
            ErrorCategory.NETWORK: "Check network connection and retry",
            ErrorCategory.RESOURCE: "Free up system resources (disk/memory) and retry",
            ErrorCategory.DEPENDENCY: f"Install missing dependency: {pattern.fix_command or 'check error message'}",
            ErrorCategory.COMPILATION: "Fix TypeScript/compilation errors in the code",
            ErrorCategory.SYNTAX: "Fix syntax errors in the code",
            ErrorCategory.RUNTIME: "Debug runtime error and fix logic",
            ErrorCategory.VALIDATION: "Fix linting/validation warnings",
            ErrorCategory.CONFIGURATION: "Check configuration files for errors",
            ErrorCategory.UNKNOWN: "Review error message and investigate",
        }

        return suggestions.get(category, "Unknown error - investigate")
